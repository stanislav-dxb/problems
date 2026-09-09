"""Triangulate stage: join pain clusters with report claims and news catalysts via local embeddings.

triangulation_score = geometric mean of pain, market and timing evidence (0..1 each) on a 0-100 scale,
so a missing leg pulls the score to zero on purpose. Strong claims/catalysts that match no cluster
become hypotheses ("reports say this is broken, nobody is complaining publicly yet").
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import timedelta
from typing import Any

import numpy as np

from . import db as dbm
from .claims import ask_whom
from .cluster import embed
from .score import normalise_growth
from .util import now_utc, parse_dt

log = logging.getLogger("scout.triangulate")

CLAIM_TYPE_WEIGHT = {"market_size": 1.0, "structural_gap": 0.9, "growth_rate": 0.8, "demand_shift": 0.7,
                     "incumbent_weakness": 0.6, "regulatory_change": 0.5, "technology_shift": 0.5}


def geometric_mean(values: list[float]) -> float:
    if not values or any(v <= 0 for v in values):
        return 0.0
    return math.exp(sum(math.log(v) for v in values) / len(values))


def pain_leg(cluster: sqlite3.Row, members: list[sqlite3.Row], volume_cap: int = 50) -> float:
    pains = [m["pain_score"] for m in members if m["pain_score"]]
    volume = min(1.0, math.log1p(len(members)) / math.log1p(volume_cap))
    pain = ((sum(pains) / len(pains)) - 1) / 4 if pains else 0.0
    return round(0.5 * volume + 0.3 * pain + 0.2 * normalise_growth(cluster["growth_30d"]), 4)


def market_leg(matches: list[tuple[sqlite3.Row, float]]) -> float:
    best = 0.0
    for claim, sim in matches:
        w = CLAIM_TYPE_WEIGHT.get(claim["claim_type"], 0.4)
        best = max(best, (claim["confidence_in_source"] or 3) / 5 * w * sim)
    return round(min(1.0, best), 4)


def timing_leg(matches: list[tuple[sqlite3.Row, float]], now=None) -> float:
    now = now or now_utc()
    best = 0.0
    for cat, sim in matches:
        dt = parse_dt(cat["published_at"])
        age = (now - dt).days if dt else 90
        recency = math.exp(-max(0, age) / 90)
        best = max(best, (cat["catalyst_strength"] or 1) / 5 * recency * sim)
    return round(min(1.0, best), 4)


def _matches(query: np.ndarray, pool: np.ndarray, threshold: float) -> list[tuple[int, float]]:
    """(index, similarity) pairs within cosine distance threshold, best first."""
    if len(pool) == 0:
        return []
    sims = pool @ query
    idx = np.where(1 - sims <= threshold)[0]
    return sorted(((int(i), float(sims[i])) for i in idx), key=lambda t: -t[1])


def triangulate(cfg: dict, conn: sqlite3.Connection) -> dict[str, Any]:
    tcfg = cfg.get("triangulation", {})
    threshold = float(tcfg.get("distance_threshold", 0.45))
    min_cluster = int(cfg.get("scoring", {}).get("min_cluster_size", 3))
    model = cfg.get("clustering", {}).get("embedding_model")
    clusters = list(conn.execute("SELECT * FROM clusters WHERE item_count >= ? ORDER BY id", (min_cluster,)))
    claims = dbm.relevant_claims(conn)
    cats = dbm.catalysts(conn)
    stats = {"clusters": len(clusters), "claims": len(claims), "catalysts": len(cats), "triangulated": 0,
             "with_all_legs": 0, "hypotheses": 0}
    run_id = dbm.start_run(conn, "triangulate")
    conn.execute("DELETE FROM triangulations")
    conn.execute("DELETE FROM hypotheses")
    if not clusters and not claims and not cats:
        dbm.finish_run(conn, run_id, notes=str(stats))
        conn.commit()
        return stats
    texts = ([c["canonical_summary"] or c["label"] or "" for c in clusters]
             + [c["claim_summary"] or "" for c in claims] + [c["summary"] or c["title"] or "" for c in cats])
    vecs = embed(texts, model) if texts else np.zeros((0, 1), dtype=np.float32)
    cv = vecs[:len(clusters)]
    clv = vecs[len(clusters):len(clusters) + len(claims)]
    cav = vecs[len(clusters) + len(claims):]
    claim_hit: set[int] = set()
    cat_hit: set[int] = set()
    for i, c in enumerate(clusters):
        cm = [(claims[j], s) for j, s in _matches(cv[i], clv, threshold)]
        km = [(cats[j], s) for j, s in _matches(cv[i], cav, threshold)]
        claim_hit.update(claims[j]["id"] for j, _ in _matches(cv[i], clv, threshold))
        cat_hit.update(cats[j]["id"] for j, _ in _matches(cv[i], cav, threshold))
        members = dbm.cluster_members(conn, c["id"])
        p, m, t = pain_leg(c, members), market_leg(cm), timing_leg(km)
        score = round(100 * geometric_mean([p, m, t]), 1)
        best = max(cm, key=lambda x: (CLAIM_TYPE_WEIGHT.get(x[0]["claim_type"], 0.4) * (x[0]["confidence_in_source"] or 3), x[1]),
                   default=None)
        geos: set[str] = set()
        for row, _ in cm + km:
            try:
                geos.update(json.loads(row["geography"] or "[]"))
            except ValueError:
                pass
        why_now = "; ".join(f"{k['summary']} ({(k['published_at'] or '')[:10]}, {k['catalyst_type']}, strength {k['catalyst_strength']})"
                            for k, _ in sorted(km, key=lambda x: (-(x[0]["catalyst_strength"] or 0), x[0]["published_at"] or ""))[:5])
        msource = None
        if best is not None:
            nums = json.loads(best[0]["numbers"] or "[]")
            fig = nums[0]["value"] if nums else "no figure"
            msource = f"{best[0]['publisher']}: {best[0]['report_title']} — {fig} ({best[0]['claim_type']}, confidence {best[0]['confidence_in_source']}/5) {best[0]['report_url']}"
        legs = {"pain": p, "market": m, "timing": t}
        weakest = min(legs, key=legs.get)
        fill = {"pain": "add subreddits or query terms for this domain and re-run collect",
                "market": "no report claim matched: add a World Bank query or publisher feed for this domain",
                "timing": "no catalyst matched: add a Google News query for this domain and its regulators"}[weakest]
        dbm.insert_triangulation(conn, {
            "cluster_id": c["id"], "pain_evidence": p, "market_evidence": m, "timing_evidence": t,
            "triangulation_score": score, "best_claim_id": best[0]["id"] if best else None,
            "claim_ids": [x[0]["id"] for x in cm], "catalyst_ids": [x[0]["id"] for x in km],
            "market_size_source": msource, "why_now": why_now or None,
            "evidence_gaps": f"weakest leg: {weakest} ({legs[weakest]:.2f}); {fill}", "geography": sorted(geos)})
        stats["triangulated"] += 1
        if p > 0 and m > 0 and t > 0:
            stats["with_all_legs"] += 1
    # reverse: strong signals with no pain cluster
    min_conf = int(tcfg.get("min_claim_confidence_for_hypothesis", 3))
    min_strength = int(tcfg.get("min_catalyst_strength_for_hypothesis", 4))
    for cl in claims:
        if cl["id"] in claim_hit or cl["claim_type"] not in ("market_size", "structural_gap") or (cl["confidence_in_source"] or 0) < min_conf:
            continue
        dbm.insert_hypothesis(conn, {"kind": "report", "ref_id": cl["id"], "summary": cl["claim_summary"],
                                     "domain": cl["domain"], "geography": json.loads(cl["geography"] or "[]"),
                                     "strength": cl["confidence_in_source"], "ask_whom": ask_whom(f"{cl['domain'] or ''} {cl['claim_summary']}"),
                                     "source_url": cl["report_url"]})
        stats["hypotheses"] += 1
    for k in cats:
        if k["id"] in cat_hit or (k["catalyst_strength"] or 0) < min_strength:
            continue
        dbm.insert_hypothesis(conn, {"kind": "news", "ref_id": k["id"], "summary": k["summary"], "domain": k["domain"],
                                     "geography": json.loads(k["geography"] or "[]"), "strength": k["catalyst_strength"],
                                     "ask_whom": ask_whom(f"{k['domain'] or ''} {k['summary']}"), "source_url": k["url"]})
        stats["hypotheses"] += 1
    dbm.finish_run(conn, run_id, notes=str(stats))
    conn.commit()
    log.info("triangulate: %s", stats)
    return stats
