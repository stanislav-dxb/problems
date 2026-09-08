"""Claude API wrapper: one place for calls, token logging, cost estimates and JSON parsing.

Set SCOUT_LLM=stub to run the whole pipeline without any API calls (deterministic
keyword heuristics). Stub output is clearly marked and only meant for tests and smoke runs.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from typing import Any

from . import db as dbm

log = logging.getLogger("scout.llm")

# USD per million tokens: (input, output). Cache read = 0.1x input, cache write = 1.25x input.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
}
DEFAULT_PRICE = (5.0, 25.0)


def price_for(model: str) -> tuple[float, float]:
    for k, v in PRICING.items():
        if model.startswith(k):
            return v
    return DEFAULT_PRICE


def estimate_cost(model: str, input_tokens: int, output_tokens: int,
                  cache_read: int = 0, cache_write: int = 0) -> float:
    pin, pout = price_for(model)
    return (input_tokens * pin + output_tokens * pout + cache_read * pin * 0.1
            + cache_write * pin * 1.25) / 1_000_000


class LLMError(RuntimeError):
    pass


def _has_credentials() -> bool:
    """True if the SDK has some credential source: env key/token, or an `ant auth login` profile."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    if os.environ.get("ANTHROPIC_PROFILE"):
        return True
    from pathlib import Path
    return (Path.home() / ".config" / "anthropic").exists()


class LLMUnavailable(LLMError):
    """No credentials or SDK available."""


# ---------------------------------------------------------------- JSON parsing

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Parse the first JSON value from a model response.

    Tolerates markdown fences, leading/trailing prose, and stray text after the value.
    Raises ValueError if nothing parseable is found.
    """
    if text is None:
        raise ValueError("empty response")
    s = text.strip()
    if not s:
        raise ValueError("empty response")
    candidates = [s]
    for m in _FENCE_RE.finditer(s):
        candidates.append(m.group(1).strip())
    dec = json.JSONDecoder()
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass
        # find first '[' or '{' and raw-decode from there
        starts = [i for i in (cand.find("["), cand.find("{")) if i >= 0]
        for start in sorted(starts):
            try:
                val, _ = dec.raw_decode(cand[start:])
                return val
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON value found in response")


# ---------------------------------------------------------------- backends

class BaseLLM:
    name = "base"
    model = "none"

    def complete(self, system: str, user: str, purpose: str, max_tokens: int = 16000) -> str:
        raise NotImplementedError

    def complete_json(self, system: str, user: str, purpose: str, max_tokens: int = 16000,
                      retries: int = 1) -> Any:
        """Call the model and parse JSON; retry once with a stricter reminder on failure."""
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            u = user if attempt == 0 else (
                user + "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON value, "
                       "no prose, no markdown fences.")
            text = self.complete(system, u, purpose, max_tokens=max_tokens)
            try:
                return extract_json(text)
            except ValueError as e:
                last_err = e
                log.warning("%s: malformed JSON (attempt %d): %s", purpose, attempt + 1, e)
        raise LLMError(f"{purpose}: malformed JSON after {retries + 1} attempts: {last_err}")


FALLBACK_BETA = "server-side-fallback-2026-07-01"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = {"classify": "low", "cluster_label": "low", "evaluate": "high"}


def build_request(model: str, system: str, user: str, purpose: str, max_tokens: int,
                  effort: dict[str, str] | None = None, fallbacks: str | None = "default") -> dict[str, Any]:
    """Build the Messages API request for one call. Pure function so it can be unit-tested.

    - Thinking stays on (adaptive; the default on Claude Opus 5). Cost is controlled with
      output_config.effort per purpose instead of disabling thinking.
    - The system prompt is a cache breakpoint (stable prefix, varying user payload after it).
    - fallbacks="default" re-runs a request that the model's safety classifiers decline on
      Anthropic's recommended fallback model, server-side (beta header FALLBACK_BETA).
    """
    lvl = (effort or {}).get(purpose) or DEFAULT_EFFORT.get(purpose, "high")
    if lvl not in EFFORT_LEVELS:
        raise ValueError(f"unknown effort {lvl!r} for {purpose}; use one of {EFFORT_LEVELS}")
    req: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": lvl},
    }
    if fallbacks and fallbacks != "none":
        req["fallbacks"] = fallbacks
        req["betas"] = [FALLBACK_BETA]
    return req


class ClaudeLLM(BaseLLM):
    name = "claude"

    def __init__(self, model: str, conn: sqlite3.Connection | None = None,
                 effort: dict[str, str] | None = None, fallbacks: str | None = "default"):
        try:
            import anthropic  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise LLMUnavailable("anthropic SDK not installed (pip install anthropic)") from e
        if not _has_credentials():
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set (put it in .env, or run `ant auth login`)")
        import anthropic
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=3)
        self.model = model
        self.conn = conn
        self.effort = dict(DEFAULT_EFFORT, **(effort or {}))
        self.fallbacks = fallbacks

    def complete(self, system: str, user: str, purpose: str, max_tokens: int = 16000) -> str:
        a = self._anthropic
        req = build_request(self.model, system, user, purpose, max_tokens, self.effort, self.fallbacks)
        t0 = time.monotonic()
        try:
            resp = self.client.beta.messages.create(**req)
        except a.RateLimitError as e:
            self._log(purpose, t0, ok=0, error=f"rate_limited: {e}")
            raise LLMError(f"rate limited: {e}") from e
        except a.AuthenticationError as e:
            self._log(purpose, t0, ok=0, error="auth")
            raise LLMUnavailable(f"authentication failed: {e}") from e
        except a.APIStatusError as e:
            self._log(purpose, t0, ok=0, error=f"status {e.status_code}: {e.message}")
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            self._log(purpose, t0, ok=0, error=f"connection: {e}")
            raise LLMError(f"connection error: {e}") from e
        except TypeError as e:
            # The SDK raises TypeError when no credential source (env, profile) resolves.
            if "authentication" not in str(e).lower():
                raise
            self._log(purpose, t0, ok=0, error="auth")
            raise LLMUnavailable(f"authentication failed: {e}") from e

        usage = resp.usage
        served_by = resp.model or self.model
        self._log(purpose, t0, ok=1, error=None, model=served_by,
                  input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                  cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
                  cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0)
        if served_by != self.model:
            log.info("%s: served by fallback model %s", purpose, served_by)
        if resp.stop_reason == "refusal":
            det = getattr(resp, "stop_details", None)
            why = f" ({det.category}: {det.explanation})" if det is not None and getattr(det, "category", None) else ""
            raise LLMError(f"model declined the request{why}")
        if resp.stop_reason == "max_tokens":
            log.warning("%s: response hit max_tokens=%d; output may be truncated", purpose, max_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")

    def _log(self, purpose: str, t0: float, ok: int, error: str | None, model: str | None = None,
             input_tokens: int = 0, output_tokens: int = 0, cache_read: int = 0,
             cache_write: int = 0) -> None:
        model = model or self.model
        cost = estimate_cost(model, input_tokens, output_tokens, cache_read, cache_write)
        ms = int((time.monotonic() - t0) * 1000)
        log.info("%s: %s in=%d out=%d cache_read=%d cost=$%.4f %dms%s", purpose, model,
                 input_tokens, output_tokens, cache_read, cost, ms, "" if ok else f" ERROR {error}")
        if self.conn is not None:
            dbm.log_api_call(self.conn, purpose=purpose, model=model,
                             input_tokens=input_tokens, output_tokens=output_tokens,
                             cache_read_tokens=cache_read, cache_write_tokens=cache_write,
                             cost_usd=cost, duration_ms=ms, ok=ok, error=error)
            self.conn.commit()


class StubLLM(BaseLLM):
    """Deterministic keyword heuristics standing in for Claude. Tests and smoke runs only."""
    name = "stub"
    model = "stub"

    PROBLEM_HINTS = ("is there a tool", "why is there no", "i wish", "how do you handle", "struggle",
                     "spreadsheet", "manually", "pain", "looking for a solution", "workaround",
                     "frustrat", "annoying", "waste", "hours", "can't", "cannot", "doesn't work",
                     "no way to", "impossible", "нет", "вручную", "костыль", "يدويا", "manually")
    MONEY_HINTS = ("$", "€", "£", "₹", "usd", "cost", "paid", "pay", "price", "expensive", "fee", "revenue")

    def __init__(self, conn: sqlite3.Connection | None = None):
        self.conn = conn

    def complete(self, system: str, user: str, purpose: str, max_tokens: int = 16000) -> str:
        payload = None
        try:
            payload = extract_json(user)
        except ValueError:
            pass
        if purpose == "classify":
            return json.dumps(self._classify(payload or []))
        if purpose == "cluster_label":
            summaries = (payload or {}).get("summaries", []) if isinstance(payload, dict) else []
            first = summaries[0] if summaries else "Unlabelled cluster"
            return json.dumps({"label": first[:60], "canonical_summary": first,
                               "domain": (payload or {}).get("domain") or "general"})
        if purpose == "evaluate":
            n = int((payload or {}).get("item_count", 5)) if isinstance(payload, dict) else 5
            s = 2 + min(3, n // 5)
            return json.dumps({
                "market_size_estimate": "unknown (stub)",
                "market_size_reasoning": "STUB: no model call was made.",
                "monopoly_potential": s, "location_independent": s, "capital_light": s,
                "measurable_90d": s, "path_to_1b": "STUB: no model call was made.",
                "path_to_1b_score": s, "what_would_kill_it": "STUB", "quickest_test": "STUB",
                "cross_market_advantage": "",
            })
        return "{}"

    def _classify(self, items: list[dict]) -> list[dict]:
        out = []
        for it in items:
            text = f"{it.get('title', '')}\n{it.get('body', '')}".lower()
            is_problem = any(h in text for h in self.PROBLEM_HINTS) and len(text) > 60
            summary = (it.get("title") or it.get("body") or "")[:140].replace("\n", " ")
            out.append({
                "idx": it.get("idx"),
                "is_problem": is_problem,
                "domain": "general" if is_problem else None,
                "who_has_it": "unknown" if is_problem else None,
                "problem_summary": f"[stub] {summary}" if is_problem else None,
                "pain_score": 3 if is_problem else None,
                "money_mentioned": any(m in text for m in self.MONEY_HINTS),
                "workaround_described": "workaround" in text or "manually" in text,
                "solution_requested": "is there" in text or "looking for" in text,
                "existing_solutions_named": [],
            })
        return out


def get_llm(cfg: dict, conn: sqlite3.Connection | None = None) -> BaseLLM:
    backend = (os.environ.get("SCOUT_LLM") or "claude").lower()
    if backend == "stub":
        log.warning("SCOUT_LLM=stub: using deterministic stub instead of Claude. Output is NOT real analysis.")
        return StubLLM(conn)
    lcfg = cfg.get("llm", {}) or {}
    return ClaudeLLM(cfg.get("model", "claude-opus-5"), conn, effort=lcfg.get("effort"),
                     fallbacks=lcfg.get("fallbacks", "default"))
