"""Score stage: rank clusters by measurable evidence. No model calls.

Each component is normalised to 0..1 and combined with the weights in config.yaml -> scoring.weights:
  volume      log-scaled item count
  growth      last 30 days vs the 30 before (flat = 0.5)
  sources     how many different sources the problem shows up in
  languages   how many of EN/RU/AR/HI it shows up in (cross-market signal)
  pain        mean pain score of members
  money       share of members that mention money
  demand      share of members that explicitly ask for a solution
  workaround  share of members describing a manual workaround
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections import Counter
from typing import Any

from . import db as dbm

log = logging.getLogger("scout.score")

COMPONENTS = ("volume", "growth", "sources", "languages", "pain", "money", "demand", "workaround")


def normalise_growth(growth: float | None) -> float:
    """Map a growth ratio to 0..1: 0.25x -> 0, 1x (flat) -> 0.5, 4x+ -> 1."""
    if growth is None or growth <= 0:
        return 0.0
    return max(0.0, min(1.0, math.log2(max(growth, 0.25)) / 4 + 0.5))


def components_for(cluster: sqlite3.Row, members: list[sqlite3.Row], volume_cap: int = 50) -> dict[str, float]:
    n = max(1, len(members))
    langs = {(m["item_language"] or "und") for m in members} & {"en", "ru", "ar", "hi"}
    try:
        n_sources = len(json.loads(cluster["sources"] or "{}"))
    except ValueError:
        n_sources = 1
    pains = [m["pain_score"] for m in members if m["pain_score"]]
    return {
        "volume": min(1.0, math.log1p(len(members)) / math.log1p(volume_cap)),
        "growth": normalise_growth(cluster["growth_30d"]),
        "sources": min(1.0, (n_sources - 1) / 3),
        "languages": min(1.0, (len(langs) - 1) / 3) if langs else 0.0,
        "pain": ((sum(pains) / len(pains)) - 1) / 4 if pains else 0.0,
        "money": sum(1 for m in members if m["money_mentioned"]) / n,
        "demand": sum(1 for m in members if m["solution_requested"]) / n,
        "workaround": sum(1 for m in members if m["workaround_described"]) / n,
    }


def compute_overall_score(components: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted sum of 0..1 components on a 0-100 scale; weights are normalised to sum to 1."""
    wsum = sum(float(w) for w in weights.values()) or 1.0
    total = sum(float(weights.get(k, 0)) * max(0.0, min(1.0, float(components.get(k, 0)))) for k in COMPONENTS)
    return round(100.0 * total / wsum, 1)


def named_solutions(members: list[sqlite3.Row], top: int = 10) -> list[tuple[str, int]]:
    c: Counter = Counter()
    for m in members:
        try:
            for s in json.loads(m["existing_solutions_named"] or "[]"):
                c[str(s)] += 1
        except (TypeError, ValueError):
            pass
    return c.most_common(top)


def score(cfg: dict, conn: sqlite3.Connection) -> dict[str, Any]:
    scfg = cfg.get("scoring", {})
    weights = scfg.get("weights", {})
    min_size = int(scfg.get("min_cluster_size", 3))
    clusters = list(conn.execute("SELECT * FROM clusters WHERE item_count >= ? ORDER BY item_count DESC", (min_size,)))
    stats = {"clusters": len(clusters), "scored": 0}
    run_id = dbm.start_run(conn, "score")
    conn.execute("DELETE FROM scores")
    for c in clusters:
        members = dbm.cluster_members(conn, c["id"])
        comps = components_for(c, members, int(scfg.get("volume_cap", 50)))
        sols = named_solutions(members)
        row = {"cluster_id": c["id"], **comps, "overall_score": compute_overall_score(comps, weights),
               "existing_solutions": sols, "competition": len(sols)}
        dbm.insert_score(conn, row)
        stats["scored"] += 1
    dbm.finish_run(conn, run_id, notes=str(stats))
    conn.commit()
    log.info("score: %d clusters scored (min size %d)", stats["scored"], min_size)
    return stats
