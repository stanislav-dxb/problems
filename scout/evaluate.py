"""Evaluate stage: one model call per cluster against the founder's criteria, with the cluster's
matched report claims and news catalysts as context. Sequential. Skipped under the rules backend."""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections import Counter
from typing import Any

from . import db as dbm
from . import llm
from .score import normalise_growth
from .util import truncate_words

log = logging.getLogger("scout.evaluate")

FOUNDER_CRITERIA = """The founder's criteria (apply them exactly as written):

1. Location-independent — the company can be run from any of several hubs; it is not welded to one city's economy.
2. Runs without the founder eventually — product, platform, or systematised service.
3. Startable with founder-controlled capital — no raise required to reach first revenue.
4. Measurable within 90 days — a number can move in one quarter.
5. Credible written path to $1B valuation — one paragraph, with rough arithmetic (buyers × annual spend × achievable share × multiple)."""

FOUNDER_CONTEXT = """Founder context: operates across Russian-speaking, Gulf (UAE/Saudi), Indian, and Chinese markets; speaks Russian, English, Arabic, Hindi; based in Dubai but seeking geographic flexibility; background in aesthetics clinics, decorative construction surfaces, marketing/HR operations. Flag any cluster where this cross-market position is a specific advantage (e.g. arbitrage of a solution that exists in one market but not another, or a problem concentrated in the founder's markets)."""

SYSTEM_PROMPT = f"""You evaluate clusters of recurring problems for a founder looking for problems that could support a $1B+ company. You produce a sober, evidence-based assessment — not a pitch. Be concrete, sceptical, and quantitative. Use the evidence given (pain quotes, report claims, news catalysts); do not invent quotes or figures, and say when the evidence is thin.

{FOUNDER_CRITERIA}

{FOUNDER_CONTEXT}

Reply with ONLY a JSON object with exactly these fields (no prose, no markdown fences):
{{
  "market_size_estimate": "<one line, e.g. '$3-5B annual spend globally'>",
  "market_size_reasoning": "<show the arithmetic: number of buyers × annual spend per buyer, with the sources of each assumption>",
  "market_size_source": "<which attached report claim and figure you relied on, or 'none attached; estimate from general knowledge'>",
  "monopoly_potential": <1-5: could one company own this? network effects, data moats, switching costs, regulation>,
  "location_independent": <1-5 per criterion 1>,
  "runs_without_founder": <1-5 per criterion 2>,
  "capital_light": <1-5 per criterion 3: 5 = first revenue reachable with founder capital only>,
  "measurable_90d": <1-5 per criterion 4>,
  "path_to_1b": "<one paragraph with rough arithmetic: buyers × annual spend × achievable share × multiple>",
  "path_to_1b_score": <1-5: how credible that path is>,
  "why_now": "<one paragraph citing the attached catalysts by date; if none are attached say so>",
  "what_would_kill_it": "<the single most likely failure>",
  "quickest_test": "<something doable in under two weeks for under $500 that would move belief>",
  "evidence_gaps": "<which of the three legs (pain, market, timing) is weakest and what would fill it>",
  "corridor_advantage": "<how the founder's cross-market position specifically helps here, or empty string if it does not>"
}}
Scores are integers 1-5. Never include personal names."""


def compute_overall_score(scores: dict[str, Any], growth_30d: float | None, triangulation: float | None,
                          weights: dict[str, float]) -> float:
    """0-100. 1-5 scores map to 0..1 via (s-1)/4; triangulation is 0..100 -> 0..1; growth is normalised."""
    def s(k: str) -> float:
        try:
            return max(0.0, min(1.0, (float(scores.get(k)) - 1) / 4))
        except (TypeError, ValueError):
            return 0.0
    total = (weights.get("path_to_1b", 0) * s("path_to_1b_score")
             + weights.get("monopoly_potential", 0) * s("monopoly_potential")
             + weights.get("triangulation_score", 0) * max(0.0, min(1.0, (triangulation or 0.0) / 100))
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


def _loads(s: str | None, default: Any) -> Any:
    try:
        return json.loads(s) if s else default
    except ValueError:
        return default


def build_user_message(conn: sqlite3.Connection, cluster: sqlite3.Row, members: list[sqlite3.Row],
                       tri: sqlite3.Row | None, max_summaries: int = 15, max_quotes: int = 5) -> str:
    sols: Counter = Counter()
    for m in members:
        for x in _loads(m["existing_solutions_named"], []):
            sols[str(x)] += 1
    claims, cats = [], []
    if tri is not None:
        ids = _loads(tri["claim_ids"], [])[:5]
        if ids:
            q = ",".join("?" * len(ids))
            for c in conn.execute(f"SELECT c.*, r.publisher, r.title AS report_title, r.url AS report_url FROM report_claims c "
                                  f"JOIN reports r ON r.id = c.report_id WHERE c.id IN ({q})", ids):
                claims.append({"type": c["claim_type"], "claim": c["claim_summary"], "figures": _loads(c["numbers"], [])[:3],
                               "publisher": c["publisher"], "report": c["report_title"], "confidence": c["confidence_in_source"],
                               "geography": _loads(c["geography"], [])})
        ids = _loads(tri["catalyst_ids"], [])[:5]
        if ids:
            q = ",".join("?" * len(ids))
            for k in conn.execute(f"SELECT n.*, a.title, a.published_at, a.feed FROM news_catalysts n JOIN news_articles a "
                                  f"ON a.id = n.article_id WHERE n.id IN ({q})", ids):
                cats.append({"type": k["catalyst_type"], "headline": k["summary"], "date": (k["published_at"] or "")[:10],
                             "strength": k["catalyst_strength"], "horizon": k["time_horizon"], "source": k["feed"],
                             "geography": _loads(k["geography"], [])})
    payload = {
        "label": cluster["label"], "canonical_summary": cluster["canonical_summary"], "domain": cluster["domain"],
        "item_count": cluster["item_count"], "sources": _loads(cluster["sources"], {}), "growth_30d": cluster["growth_30d"],
        "first_seen": cluster["first_seen"], "last_seen": cluster["last_seen"],
        "languages": dict(Counter(m["item_language"] or "und" for m in members)),
        "money_mentioned_share": round(sum(1 for m in members if m["money_mentioned"]) / max(1, len(members)), 2),
        "workaround_share": round(sum(1 for m in members if m["workaround_described"]) / max(1, len(members)), 2),
        "solution_requested_share": round(sum(1 for m in members if m["solution_requested"]) / max(1, len(members)), 2),
        "existing_solutions_named": [s for s, _ in sols.most_common(15)],
        "member_summaries": [{"summary": m["problem_summary"], "pain": m["pain_score"], "who": m["who_has_it"],
                              "source": m["source"], "language": m["item_language"]} for m in members[:max_summaries]],
        "verbatim_quotes": [{"source": m["source"], "text": truncate_words((m["body"] or "").replace("\n", " "), 60)}
                            for m in members[:max_quotes]],
        "triangulation": None if tri is None else {
            "pain_evidence": tri["pain_evidence"], "market_evidence": tri["market_evidence"],
            "timing_evidence": tri["timing_evidence"], "score": tri["triangulation_score"],
            "market_size_source": tri["market_size_source"], "evidence_gaps": tri["evidence_gaps"]},
        "attached_report_claims": claims, "attached_news_catalysts": cats,
    }
    return "Evaluate this problem cluster:\n\n" + json.dumps(payload, ensure_ascii=False, indent=1)


def evaluate_cluster(conn: sqlite3.Connection, cluster: sqlite3.Row, members: list[sqlite3.Row],
                     tri: sqlite3.Row | None, weights: dict, model: str) -> dict[str, Any]:
    data = llm.complete_json(SYSTEM_PROMPT, build_user_message(conn, cluster, members, tri), model,
                             stage="evaluate", items=1, retries=1)
    if not isinstance(data, dict):
        raise llm.LLMError("evaluation is not a JSON object")
    text = lambda k, n: str(data.get(k) or "")[:n]  # noqa: E731
    row = {
        "cluster_id": cluster["id"], "member_hash": cluster["member_hash"],
        "member_item_ids": sorted(m["item_id"] for m in members), "item_count": cluster["item_count"],
        "market_size_estimate": text("market_size_estimate", 300), "market_size_reasoning": text("market_size_reasoning", 3000),
        "market_size_source": text("market_size_source", 1000),
        "monopoly_potential": _score(data.get("monopoly_potential")), "location_independent": _score(data.get("location_independent")),
        "runs_without_founder": _score(data.get("runs_without_founder")), "capital_light": _score(data.get("capital_light")),
        "measurable_90d": _score(data.get("measurable_90d")), "path_to_1b": text("path_to_1b", 4000),
        "path_to_1b_score": _score(data.get("path_to_1b_score")), "why_now": text("why_now", 2000),
        "what_would_kill_it": text("what_would_kill_it", 1500), "quickest_test": text("quickest_test", 1500),
        "evidence_gaps": text("evidence_gaps", 1500), "corridor_advantage": text("corridor_advantage", 1500),
        "model": model, "raw_json": data,
    }
    row["overall_score"] = compute_overall_score(row, cluster["growth_30d"], tri["triangulation_score"] if tri else None, weights)
    return row


def _reusable(prior: list[sqlite3.Row], cluster: sqlite3.Row, member_ids: list[int], growth_limit: float) -> sqlite3.Row | None:
    """A prior evaluation with the same members, or >= 80% overlap and growth under the re-evaluate threshold."""
    ids = set(member_ids)
    for e in prior:
        if e["member_hash"] and e["member_hash"] == cluster["member_hash"]:
            return e
    for e in prior:
        old = set(_loads(e["member_item_ids"], []))
        if not old:
            continue
        overlap = len(ids & old) / max(1, len(ids | old))
        grew = (len(ids) - len(old)) / max(1, len(old))
        if overlap >= 0.8 and grew < growth_limit:
            return e
    return None


def evaluate(cfg: dict, conn: sqlite3.Connection, force: bool = False, limit: int | None = None) -> dict[str, Any]:
    ecfg = cfg.get("evaluate", {})
    min_size = int(ecfg.get("min_cluster_size", 3))
    weights = ecfg.get("weights", {})
    growth_limit = float(ecfg.get("reevaluate_on_growth", 0.20))
    stats: dict[str, Any] = {"candidates": 0, "evaluated": 0, "reused": 0, "failed": 0, "skipped": None}
    if llm.backend_name() == "rules":
        stats["skipped"] = "llm.backend is 'rules': evaluate needs a model (set llm.backend to claude_code or anthropic_api)"
        log.info("evaluate: skipped — %s", stats["skipped"])
        return stats
    model = llm.evaluate_model()
    stats["model"] = model
    evaluated = dbm.latest_evaluations(conn)
    clusters = [c for c in conn.execute("SELECT * FROM clusters WHERE item_count >= ? ORDER BY item_count DESC", (min_size,))
                if force or c["id"] not in evaluated]
    if limit:
        clusters = clusters[:limit]
    stats["candidates"] = len(clusters)
    if not clusters:
        log.info("evaluate: nothing to do")
        return stats
    run_id = dbm.start_run(conn, "evaluate")
    prior = dbm.prior_evaluations(conn)
    tri = dbm.triangulations_by_cluster(conn)
    for c in clusters:
        members = dbm.cluster_members(conn, c["id"])
        member_ids = [m["item_id"] for m in members]
        if not force:
            reuse = _reusable(prior, c, member_ids, growth_limit)
            if reuse is not None:
                row = {k: reuse[k] for k in reuse.keys() if k not in ("id", "cluster_id", "reused_from")}
                row.update({"cluster_id": c["id"], "reused_from": reuse["id"], "member_hash": c["member_hash"],
                            "member_item_ids": sorted(member_ids), "item_count": c["item_count"]})
                dbm.insert_evaluation(conn, row)
                stats["reused"] += 1
                conn.commit()
                continue
        try:
            row = evaluate_cluster(conn, c, members, tri.get(c["id"]), weights, model)
        except llm.LLMUnavailable:
            raise
        except llm.LLMError as e:
            try:  # once more, per the retry policy
                row = evaluate_cluster(conn, c, members, tri.get(c["id"]), weights, model)
            except llm.LLMError as e2:
                stats["failed"] += 1
                log.error("evaluate: cluster %d (%s) failed twice: %s / %s", c["id"], c["label"], e, e2)
                continue
        dbm.insert_evaluation(conn, row)
        stats["evaluated"] += 1
        conn.commit()
        log.info("evaluate: cluster %d %r -> %.1f", c["id"], c["label"], row["overall_score"])
    dbm.finish_run(conn, run_id, notes=str(stats))
    conn.commit()
    return stats
