"""Evaluate stage: score each sizeable cluster against the founder's criteria with Claude."""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections import Counter
from typing import Any

from . import db as dbm
from .llm import BaseLLM, LLMError
from .util import truncate_words

log = logging.getLogger("scout.evaluate")

FOUNDER_CRITERIA = """The founder's criteria (apply them exactly as written):

1. Location-independent — the company can be run from any of several hubs; it is not welded to one city's economy.
2. Runs without the founder eventually — product, platform, or systematised service.
3. Startable with founder-controlled capital — no raise required to reach first revenue.
4. Measurable within 90 days — a number can move in one quarter.
5. Credible written path to $1B valuation — one paragraph, with rough arithmetic (buyers × annual spend × achievable share × multiple)."""

FOUNDER_CONTEXT = """Founder context: operates across Russian-speaking, Gulf (UAE/Saudi), Indian, and Chinese markets; speaks Russian, English, Arabic, Hindi; based in Dubai but seeking geographic flexibility; background in aesthetics clinics, decorative construction surfaces, marketing/HR operations. Flag any cluster where this cross-market position is a specific advantage (e.g. arbitrage of a solution that exists in one market but not another, or a problem concentrated in the founder's markets)."""

SYSTEM_PROMPT = f"""You evaluate clusters of recurring problems for a founder looking for problems that could support a $1B+ company. You produce a sober, evidence-based assessment — not a pitch. Be concrete, sceptical, and quantitative. Use the evidence given; do not invent quotes.

{FOUNDER_CRITERIA}

{FOUNDER_CONTEXT}

Reply with ONLY a JSON object with exactly these fields (no prose, no markdown fences):
{{
  "market_size_estimate": "<one line, e.g. '$3-5B annual spend globally'>",
  "market_size_reasoning": "<show the arithmetic: number of buyers × annual spend per buyer, with the sources of each assumption>",
  "monopoly_potential": <1-5: could one company own this? network effects, data moats, switching costs, regulation>,
  "location_independent": <1-5 per criterion 1>,
  "runs_without_founder": <1-5 per criterion 2>,
  "capital_light": <1-5 per criterion 3: 5 = first revenue reachable with founder capital only>,
  "measurable_90d": <1-5 per criterion 4>,
  "path_to_1b": "<one paragraph with rough arithmetic: buyers × annual spend × achievable share × multiple>",
  "path_to_1b_score": <1-5: how credible that path is>,
  "what_would_kill_it": "<the single most likely failure>",
  "quickest_test": "<something doable in under two weeks for under $500 that would move belief>",
  "cross_market_advantage": "<how the founder's cross-market position specifically helps here, or empty string if it does not>"
}}
Scores are integers 1-5. Never include personal names."""


def normalise_growth(growth: float | None) -> float:
    """Map a growth ratio to 0..1: 0.25x -> 0, 1x (flat) -> 0.5, 4x+ -> 1."""
    if growth is None or growth <= 0:
        return 0.0
    return max(0.0, min(1.0, math.log2(max(growth, 0.25)) / 4 + 0.5))


def compute_overall_score(scores: dict[str, Any], growth_30d: float | None, weights: dict[str, float]) -> float:
    """Weighted score on a 0-100 scale. 1-5 scores map to 0..1 via (s-1)/4; growth is normalised."""
    def s(k: str) -> float:
        v = scores.get(k)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, (v - 1) / 4))
    total = (weights.get("path_to_1b", 0) * s("path_to_1b_score")
             + weights.get("monopoly_potential", 0) * s("monopoly_potential")
             + weights.get("location_independent", 0) * s("location_independent")
             + weights.get("capital_light", 0) * s("capital_light")
             + weights.get("measurable_90d", 0) * s("measurable_90d")
             + weights.get("growth_30d", 0) * normalise_growth(growth_30d))
    wsum = sum(float(w) for w in weights.values()) or 1.0
    return round(100.0 * total / wsum, 1)


def _score(v: Any) -> int | None:
    try:
        return max(1, min(5, int(round(float(v)))))
    except (TypeError, ValueError):
        return None


def build_user_message(cluster: sqlite3.Row, members: list[sqlite3.Row], max_summaries: int = 15,
                       max_quotes: int = 5) -> str:
    sols: Counter = Counter()
    for m in members:
        try:
            for s in json.loads(m["existing_solutions_named"] or "[]"):
                sols[str(s)] += 1
        except (TypeError, ValueError):
            pass
    summaries = [{"summary": m["problem_summary"], "pain": m["pain_score"], "who": m["who_has_it"],
                  "source": m["source"], "language": m["item_language"]} for m in members[:max_summaries]]
    quotes = [{"source": m["source"], "text": truncate_words((m["body"] or "").replace("\n", " "), 60)}
              for m in members[:max_quotes]]
    payload = {
        "label": cluster["label"], "canonical_summary": cluster["canonical_summary"],
        "domain": cluster["domain"], "item_count": cluster["item_count"],
        "sources": json.loads(cluster["sources"] or "{}"), "growth_30d": cluster["growth_30d"],
        "first_seen": cluster["first_seen"], "last_seen": cluster["last_seen"],
        "languages": dict(Counter(m["item_language"] or "und" for m in members)),
        "money_mentioned_share": round(sum(1 for m in members if m["money_mentioned"]) / max(1, len(members)), 2),
        "workaround_share": round(sum(1 for m in members if m["workaround_described"]) / max(1, len(members)), 2),
        "solution_requested_share": round(sum(1 for m in members if m["solution_requested"]) / max(1, len(members)), 2),
        "existing_solutions_named": [s for s, _ in sols.most_common(15)],
        "member_summaries": summaries, "verbatim_quotes": quotes,
    }
    return "Evaluate this problem cluster:\n\n" + json.dumps(payload, ensure_ascii=False, indent=1)


def evaluate_cluster(llm: BaseLLM, cluster: sqlite3.Row, members: list[sqlite3.Row], weights: dict) -> dict[str, Any]:
    data = llm.complete_json(SYSTEM_PROMPT, build_user_message(cluster, members), purpose="evaluate",
                             max_tokens=16000, thinking=True, retries=1)
    if not isinstance(data, dict):
        raise LLMError("evaluation is not a JSON object")
    row = {
        "cluster_id": cluster["id"], "member_hash": cluster["member_hash"],
        "market_size_estimate": str(data.get("market_size_estimate") or "")[:300],
        "market_size_reasoning": str(data.get("market_size_reasoning") or "")[:3000],
        "monopoly_potential": _score(data.get("monopoly_potential")),
        "location_independent": _score(data.get("location_independent")),
        "capital_light": _score(data.get("capital_light")),
        "measurable_90d": _score(data.get("measurable_90d")),
        "path_to_1b": str(data.get("path_to_1b") or "")[:4000],
        "path_to_1b_score": _score(data.get("path_to_1b_score")),
        "what_would_kill_it": str(data.get("what_would_kill_it") or "")[:1500],
        "quickest_test": str(data.get("quickest_test") or "")[:1500],
        "cross_market_advantage": str(data.get("cross_market_advantage") or "")[:1500],
        "raw_json": data,
    }
    row["overall_score"] = compute_overall_score(row, cluster["growth_30d"], weights)
    return row


def evaluate(cfg: dict, conn: sqlite3.Connection, llm: BaseLLM, force: bool = False,
             limit: int | None = None) -> dict[str, int]:
    ecfg = cfg.get("evaluation", {})
    min_size = int(ecfg.get("min_cluster_size", 5))
    weights = ecfg.get("weights", {})
    if force:
        clusters = list(conn.execute("SELECT * FROM clusters WHERE item_count >= ? ORDER BY item_count DESC",
                                     (min_size,)))
    else:
        clusters = dbm.clusters_to_evaluate(conn, min_size)
    if limit:
        clusters = clusters[:limit]
    stats = {"candidates": len(clusters), "evaluated": 0, "reused": 0, "failed": 0}
    if not clusters:
        log.info("evaluate: nothing to do (clusters with >= %d items already evaluated or none exist)", min_size)
        return stats
    run_id = dbm.start_run(conn, "evaluate")
    for c in clusters:
        if not force:
            prev = dbm.evaluation_by_member_hash(conn, c["member_hash"])
            if prev is not None:
                row = {k: prev[k] for k in prev.keys() if k not in ("id", "cluster_id")}
                row["cluster_id"] = c["id"]
                dbm.insert_evaluation(conn, row)
                stats["reused"] += 1
                conn.commit()
                continue
        members = dbm.cluster_members(conn, c["id"])
        try:
            row = evaluate_cluster(llm, c, members, weights)
        except LLMError as e:
            stats["failed"] += 1
            log.error("evaluate: cluster %d (%s) failed: %s", c["id"], c["label"], e)
            if "authentication" in str(e).lower():
                raise
            continue
        dbm.insert_evaluation(conn, row)
        stats["evaluated"] += 1
        conn.commit()
        log.info("evaluate: cluster %d %r -> %.1f", c["id"], c["label"], row["overall_score"])
    dbm.finish_run(conn, run_id, notes=str(stats))
    conn.commit()
    return stats
