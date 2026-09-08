"""Classify stage: send items to Claude in batches and store problems rows."""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from . import db as dbm
from .llm import BaseLLM, LLMError
from .util import iso, now_utc

log = logging.getLogger("scout.classify")

SYSTEM_PROMPT = """You are the classifier for Problem Scout, a research tool that finds recurring problems people describe in public discussions (forum posts, comments, app reviews, job ads).

You receive a JSON array of items. For EACH item return one JSON object with exactly these fields:

- "idx": integer, copied from the input item.
- "is_problem": boolean. true ONLY if a person describes a difficulty that they or their organisation actually face. General opinions, product announcements, news commentary, jokes, praise, abstract debates, hypothetical musings and job descriptions with no pain described are false.
- "domain": string or null. A short industry or function label such as "construction", "ecommerce logistics", "small business accounting", "freelancing", "clinic operations", "IT administration", "cross-border payments", "developer tooling". Describe the person's activity, not the medium (never "mobile apps" or "reddit"). null when is_problem is false.
- "who_has_it": string or null. Who experiences it, e.g. "small contractors in the US", "Shopify merchants shipping internationally", "sysadmins at mid-size companies". null when is_problem is false.
- "problem_summary": string or null. ONE sentence in English, regardless of the source language, stating the underlying difficulty concretely (what they are trying to do and what gets in the way). For app reviews describe the job the person cannot get done, not just "the app crashes". null when is_problem is false.
- "pain_score": integer 1-5 or null. 1 = mild annoyance; 2 = recurring irritation; 3 = costs real time or money regularly; 4 = serious and frequent cost or risk; 5 = described as blocking, expensive, or business-critical. null when is_problem is false.
- "money_mentioned": boolean. A cost, price, fee, budget, revenue or financial loss is mentioned.
- "workaround_described": boolean. The author describes how they currently cope (spreadsheets, manual work, scripts, hiring someone, duct-taping tools).
- "solution_requested": boolean. The author asks for, searches for, or wishes for a tool, service or solution.
- "existing_solutions_named": array of strings. Products, services or named workarounds mentioned. Empty array if none.

Rules:
- Return ONLY a JSON array with exactly one object per input item, in the same order, with the same idx values. No prose, no markdown fences, no comments.
- Never include personal names or usernames in any field.
- problem_summary must be English even when the source text is Russian, Arabic, Hindi or any other language.
- Be strict about is_problem. When in doubt, false."""


def _fields_for_item(row: sqlite3.Row, idx: int, max_chars: int = 1500) -> dict[str, Any]:
    body = (row["body"] or "").strip()
    if len(body) > max_chars:
        body = body[:max_chars] + " …[truncated]"
    return {"idx": idx, "source": row["source"], "language": row["language"] or "und",
            "title": (row["title"] or "")[:200], "body": body}


def _bool(v: Any) -> int:
    if isinstance(v, str):
        return 1 if v.strip().lower() in ("true", "yes", "1") else 0
    return 1 if v else 0


def _int_or_none(v: Any, lo: int = 1, hi: int = 5) -> int | None:
    try:
        i = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, i))


def parse_classification(payload: Any, expected_idx: list[int]) -> dict[int, dict[str, Any]]:
    """Validate and normalise the classifier's JSON. Returns {idx: normalised dict} for the items
    that were parseable; missing/invalid items are simply absent (caller marks them failed)."""
    if isinstance(payload, dict):
        # Tolerate {"results": [...]} / {"items": [...]} wrappers.
        for k in ("results", "items", "classifications", "data"):
            if isinstance(payload.get(k), list):
                payload = payload[k]
                break
    if not isinstance(payload, list):
        raise ValueError("classifier output is not a JSON array")
    out: dict[int, dict[str, Any]] = {}
    for pos, obj in enumerate(payload):
        if not isinstance(obj, dict):
            continue
        idx = obj.get("idx")
        if idx is None and pos < len(expected_idx) and len(payload) == len(expected_idx):
            idx = expected_idx[pos]  # fall back to positional order
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            continue
        if idx not in expected_idx or idx in out:
            continue
        is_problem = _bool(obj.get("is_problem"))
        sols = obj.get("existing_solutions_named") or []
        if isinstance(sols, str):
            sols = [sols]
        sols = [str(s)[:100] for s in sols if s][:20]
        summary = obj.get("problem_summary")
        summary = str(summary).strip() if summary else None
        if is_problem and not summary:
            is_problem = 0  # a problem without a summary is unusable downstream
        out[idx] = {
            "is_problem": is_problem,
            "domain": (str(obj.get("domain")).strip()[:100] if obj.get("domain") else None) if is_problem else None,
            "who_has_it": (str(obj.get("who_has_it")).strip()[:200] if obj.get("who_has_it") else None) if is_problem else None,
            "problem_summary": summary[:500] if (is_problem and summary) else None,
            "pain_score": _int_or_none(obj.get("pain_score")) if is_problem else None,
            "money_mentioned": _bool(obj.get("money_mentioned")),
            "workaround_described": _bool(obj.get("workaround_described")),
            "solution_requested": _bool(obj.get("solution_requested")),
            "existing_solutions_named": sols,
        }
    return out


def classify_batch(llm: BaseLLM, rows: list[sqlite3.Row]) -> dict[int, dict[str, Any]]:
    """Classify one batch. Returns {item_id: normalised classification}. Raises LLMError."""
    idxs = list(range(len(rows)))
    payload = [_fields_for_item(r, i) for i, r in zip(idxs, rows)]
    user = "Classify these items:\n\n" + json.dumps(payload, ensure_ascii=False)
    parsed = llm.complete_json(SYSTEM_PROMPT, user, purpose="classify", max_tokens=16000, retries=1)
    normalised = parse_classification(parsed, idxs)
    return {rows[i]["id"]: v for i, v in normalised.items()}


def classify(cfg: dict, conn: sqlite3.Connection, llm: BaseLLM, limit: int | None = None,
             retry_failed: bool = False) -> dict[str, int]:
    batch_size = int(cfg.get("classify_batch_size", 20))
    rows = dbm.unclassified_items(conn, limit=limit, retry_failed=retry_failed)
    stats = {"items": len(rows), "problems": 0, "non_problems": 0, "failed": 0, "batches": 0}
    if not rows:
        log.info("classify: nothing to do")
        return stats
    run_id = dbm.start_run(conn, "classify")
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        stats["batches"] += 1
        try:
            results = classify_batch(llm, batch)
        except LLMError as e:
            log.error("classify: batch %d failed: %s", stats["batches"], e)
            results = {}
            if "authentication" in str(e).lower():
                raise
        now = iso(now_utc())
        for row in batch:
            res = results.get(row["id"])
            if res is None:
                stats["failed"] += 1
                dbm.upsert_problem(conn, {"item_id": row["id"], "is_problem": 0, "classification_failed": 1,
                                          "language": row["language"], "classified_at": now})
                continue
            res.update({"item_id": row["id"], "language": row["language"], "classified_at": now,
                        "classification_failed": 0})
            dbm.upsert_problem(conn, res)
            stats["problems" if res["is_problem"] else "non_problems"] += 1
        conn.commit()
        log.info("classify: batch %d/%d done (problems so far: %d)", stats["batches"],
                 -(-len(rows) // batch_size), stats["problems"])
    dbm.finish_run(conn, run_id, notes=str(stats))
    conn.commit()
    return stats
