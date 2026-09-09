"""Model backend for Problem Scout.

backend: claude_code   — shells out to the Claude Code CLI in headless mode (`claude -p ...`).
                         No API key; uses the logged-in Claude subscription. Calls draw on that
                         subscription's usage quota rather than a per-token bill.
         anthropic_api — the Anthropic Python SDK (ANTHROPIC_API_KEY or an `ant auth login` profile).
         rules         — no model at all: keyword classifier, evaluate stage skipped.

Never falls back from one backend to another silently: a misconfigured backend raises LLMUnavailable.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from typing import Any

log = logging.getLogger("scout.llm")

MAX_PROMPT_CHARS = 60_000
BACKENDS = ("claude_code", "anthropic_api", "rules")
CLI_MODELS = ("sonnet", "opus", "haiku")
API_MODEL_ALIASES = {"sonnet": "claude-sonnet-5", "opus": "claude-opus-5", "haiku": "claude-haiku-4-5"}

_lock = threading.Lock()
_state: dict[str, Any] = {"backend": "rules", "model": "sonnet", "evaluate_model": "opus", "timeout_s": 120,
                          "max_parallel": 2, "conn": None, "api": None, "checked": False}


class LLMError(RuntimeError):
    """A call failed (non-zero exit, timeout, error envelope, malformed reply)."""


class LLMUnavailable(LLMError):
    """The configured backend cannot be used (CLI missing / not logged in, no API credentials)."""


class PromptTooLong(LLMError):
    """system + user text exceeds MAX_PROMPT_CHARS; the caller should shrink the batch."""


# ---------------------------------------------------------------- configuration

def configure(cfg: dict, conn: sqlite3.Connection | None = None) -> None:
    lcfg = cfg.get("llm", {}) or {}
    backend = str(lcfg.get("backend", "rules")).lower()
    if backend not in BACKENDS:
        raise LLMUnavailable(f"unknown llm.backend {backend!r}; use one of {', '.join(BACKENDS)}")
    _state.update({
        "backend": backend,
        "model": str(lcfg.get("model", "sonnet")),
        "evaluate_model": str(lcfg.get("evaluate_model") or lcfg.get("model", "sonnet")),
        "timeout_s": int(lcfg.get("timeout_s", 120)),
        "max_parallel": max(1, int(lcfg.get("max_parallel", 2))),
        "conn": conn, "api": None, "checked": False, "cfg": cfg,
    })


def settings() -> dict[str, Any]:
    return {k: _state[k] for k in ("backend", "model", "evaluate_model", "timeout_s", "max_parallel")}


def backend_name() -> str:
    return _state["backend"]


def classify_model() -> str:
    return _state["model"]


def evaluate_model() -> str:
    return _state["evaluate_model"]


# ---------------------------------------------------------------- reply parsing

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)


def strip_fences(text: str) -> str:
    """Remove markdown code fences and a leading 'json' label from a model reply."""
    s = (text or "").strip()
    s = _FENCE_RE.sub("", s).strip()
    if s.lower().startswith("json"):
        rest = s[4:].lstrip()
        if rest[:1] in "[{":
            s = rest
    return s


def extract_json(text: str) -> Any:
    """Parse the first JSON value in a reply, tolerating fences and surrounding prose."""
    s = strip_fences(text)
    if not s:
        raise ValueError("empty reply")
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    for start in sorted(i for i in (s.find("["), s.find("{")) if i >= 0):
        try:
            val, _ = dec.raw_decode(s[start:])
            return val
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON value found in reply")


def parse_envelope(stdout: str) -> str:
    """Extract the result text from `claude -p --output-format json` output."""
    try:
        env = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise LLMError(f"CLI output is not a JSON envelope: {stdout[:200]!r}") from e
    if not isinstance(env, dict) or "result" not in env:
        raise LLMError(f"CLI envelope has no result field: {stdout[:200]!r}")
    result = env.get("result")
    if env.get("is_error") or str(env.get("subtype", "")).startswith("error"):
        msg = str(result)[:300]
        if "authentication" in msg.lower() or "not logged in" in msg.lower():
            raise LLMUnavailable(f"claude CLI is not logged in: {msg}")
        raise LLMError(f"CLI returned an error: {msg}")
    return str(result or "")


# ---------------------------------------------------------------- startup check

def check_backend(probe: bool = True) -> str:
    """Verify the configured backend can be used. Raises LLMUnavailable with a clear message."""
    backend = _state["backend"]
    if backend == "rules":
        return "rules backend: no model calls"
    if backend == "anthropic_api":
        _api()
        return f"anthropic_api: model {API_MODEL_ALIASES.get(_state['model'], _state['model'])}"
    exe = shutil.which("claude")
    if not exe:
        raise LLMUnavailable("claude CLI not found on PATH. Install Claude Code (https://claude.com/claude-code) "
                             "and log in once with `claude`, or set llm.backend to anthropic_api or rules.")
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise LLMUnavailable(f"`claude --version` failed: {e}") from e
    if out.returncode != 0:
        raise LLMUnavailable(f"`claude --version` exited {out.returncode}: {(out.stderr or out.stdout)[:200]}")
    version = (out.stdout or "").strip().splitlines()[0] if out.stdout else "unknown"
    if probe and not _state["checked"]:
        try:
            reply = _run_claude_code("Reply with exactly the word OK.", "OK", _state["model"], timeout=60)
        except LLMUnavailable:
            raise
        except LLMError as e:
            raise LLMUnavailable(f"claude CLI is installed ({version}) but a test call failed — not logged in? "
                                 f"Run `claude` once interactively and log in. Detail: {e}") from e
        if "ok" not in reply.lower():
            log.warning("claude probe replied unexpectedly: %r", reply[:80])
        _state["checked"] = True
    return f"claude_code: {version}, model {_state['model']} (evaluate: {_state['evaluate_model']})"


# ---------------------------------------------------------------- calls

def complete(system: str, user: str, model: str | None = None, stage: str | None = None,
             items: int | None = None) -> str:
    """One model call. Returns the reply text. Raises LLMError / LLMUnavailable / PromptTooLong."""
    backend = _state["backend"]
    if backend == "rules":
        raise LLMUnavailable("llm.backend is 'rules': no model calls are made")
    if len(system) + len(user) > MAX_PROMPT_CHARS:
        raise PromptTooLong(f"prompt is {len(system) + len(user)} chars (> {MAX_PROMPT_CHARS})")
    model = model or _state["model"]
    t0 = time.monotonic()
    try:
        if backend == "claude_code":
            text = _run_claude_code(system, user, model, _state["timeout_s"])
        else:
            text = _run_anthropic(system, user, model)
    except LLMError as e:
        _log_call(stage, backend, model, items, len(system) + len(user), 0, int((time.monotonic() - t0) * 1000), str(e)[:300])
        raise
    _log_call(stage, backend, model, items, len(system) + len(user), 1, int((time.monotonic() - t0) * 1000), None)
    return text


def complete_json(system: str, user: str, model: str | None = None, stage: str | None = None,
                  items: int | None = None, retries: int = 1) -> Any:
    """Call and parse JSON; on a malformed reply, retry once with a stricter reminder."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        u = user if attempt == 0 else (user + "\n\nYour previous reply was not valid JSON. Reply with ONLY the "
                                              "JSON value, no prose, no markdown fences.")
        text = complete(system, u, model, stage, items)
        try:
            return extract_json(text)
        except ValueError as e:
            last = e
            log.warning("%s: malformed JSON reply (attempt %d): %s", stage, attempt + 1, e)
    raise LLMError(f"{stage}: malformed JSON after {retries + 1} attempts: {last}")


def _run_claude_code(system: str, user: str, model: str, timeout: int) -> str:
    exe = shutil.which("claude") or "claude"
    cmd = [exe, "-p", user, "--system-prompt", system, "--output-format", "json", "--model", model,
           "--tools", "", "--no-session-persistence"]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}  # allow running from inside a session
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as e:
        raise LLMError(f"claude CLI timed out after {timeout}s") from e
    except OSError as e:
        raise LLMUnavailable(f"could not start claude CLI: {e}") from e
    if out.returncode != 0:
        # The envelope, when present, carries the real reason (e.g. authentication).
        try:
            return parse_envelope(out.stdout)
        except LLMUnavailable:
            raise
        except LLMError:
            pass
        raise LLMError(f"claude CLI exited {out.returncode}: {(out.stderr or out.stdout)[-300:]}")
    return parse_envelope(out.stdout)


# ---------------------------------------------------------------- anthropic_api backend (SDK)

def _has_api_credentials() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_PROFILE"):
        return True
    from pathlib import Path
    return (Path.home() / ".config" / "anthropic").exists()


def _api():
    if _state["api"] is None:
        try:
            import anthropic
        except ImportError as e:
            raise LLMUnavailable("anthropic SDK not installed (pip install anthropic) — required for llm.backend anthropic_api") from e
        if not _has_api_credentials():
            raise LLMUnavailable("anthropic_api backend needs ANTHROPIC_API_KEY (or `ant auth login`); "
                                 "use llm.backend claude_code to run on the Claude subscription instead")
        _state["api"] = anthropic.Anthropic(max_retries=3)
    return _state["api"]


def _run_anthropic(system: str, user: str, model: str) -> str:
    """Anthropic SDK path, unchanged from the earlier build: adaptive thinking, effort by stage,
    server-side fallbacks on safety declines, system prompt as a cache breakpoint."""
    import anthropic
    client = _api()
    api_model = API_MODEL_ALIASES.get(model, model)
    req: dict[str, Any] = {
        "model": api_model, "max_tokens": 16000,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "thinking": {"type": "adaptive"}, "output_config": {"effort": "low" if model == _state["model"] else "high"},
        "fallbacks": "default", "betas": ["server-side-fallback-2026-07-01"],
    }
    try:
        resp = client.beta.messages.create(**req)
    except anthropic.RateLimitError as e:
        raise LLMError(f"rate limited: {e}") from e
    except anthropic.AuthenticationError as e:
        raise LLMUnavailable(f"authentication failed: {e}") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError(f"connection error: {e}") from e
    except TypeError as e:
        if "authentication" not in str(e).lower():
            raise
        raise LLMUnavailable(f"authentication failed: {e}") from e
    if resp.stop_reason == "refusal":
        raise LLMError("model declined the request")
    return "".join(b.text for b in resp.content if b.type == "text")


# ---------------------------------------------------------------- call log

def _log_call(stage: str | None, backend: str, model: str, items: int | None, prompt_chars: int, ok: int,
              duration_ms: int, error: str | None) -> None:
    log.info("%s: %s/%s items=%s chars=%d %dms%s", stage or "call", backend, model, items, prompt_chars, duration_ms,
             "" if ok else f" ERROR {error}")
    conn = _state.get("conn")
    if conn is None:
        return
    from . import db as dbm
    with _lock:
        dbm.log_llm_call(conn, stage=stage or "call", backend=backend, model=model, items=items,
                         prompt_chars=prompt_chars, ok=ok, duration_ms=duration_ms, error=error)
        conn.commit()
