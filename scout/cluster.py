"""Cluster stage: embed problem summaries locally, agglomerative clustering, Claude labels, growth."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections import Counter
from datetime import timedelta
from typing import Any

import numpy as np

from . import db as dbm
from .llm import BaseLLM, LLMError
from .util import iso, now_utc, parse_dt

log = logging.getLogger("scout.cluster")

LABEL_SYSTEM = """You name clusters of similar problem statements for Problem Scout.
Given a list of one-sentence problem summaries that were grouped together, reply with ONLY a JSON object:
{"label": "<one line, max 12 words, names the shared problem, no company names>",
 "canonical_summary": "<one or two sentences in English describing the common problem, who has it, and why it hurts>",
 "domain": "<short industry/function label>"}
No prose, no markdown fences. Never include personal names."""


def embed(texts: list[str], model_name: str) -> np.ndarray:
    """L2-normalised sentence embeddings from a local sentence-transformers model."""
    from sentence_transformers import SentenceTransformer  # lazy: heavy import
    model = SentenceTransformer(model_name, device="cpu")
    vecs = model.encode(texts, batch_size=64, show_progress_bar=False, normalize_embeddings=True,
                        convert_to_numpy=True)
    return np.asarray(vecs, dtype=np.float32)


def cluster_labels(embeddings: np.ndarray, distance_threshold: float) -> list[int]:
    """Agglomerative clustering (average linkage, cosine distance). Deterministic for fixed input.
    Returns a label per row; labels are renumbered so that 0 is the largest cluster, ties broken by
    the smallest member index."""
    n = len(embeddings)
    if n == 0:
        return []
    if n == 1:
        return [0]
    from sklearn.cluster import AgglomerativeClustering
    model = AgglomerativeClustering(n_clusters=None, distance_threshold=float(distance_threshold),
                                    metric="cosine", linkage="average")
    raw = model.fit_predict(embeddings)
    counts = Counter(int(x) for x in raw)
    first_idx = {}
    for i, lab in enumerate(raw):
        first_idx.setdefault(int(lab), i)
    order = sorted(counts, key=lambda lab: (-counts[lab], first_idx[lab]))
    remap = {lab: new for new, lab in enumerate(order)}
    return [remap[int(x)] for x in raw]


def growth_ratio(created_dates: list, now=None, min_denominator: int = 3) -> float:
    """Items in the last 30 days divided by items in the 30 days before (denominator floored)."""
    now = now or now_utc()
    recent = prior = 0
    for d in created_dates:
        dt = parse_dt(d)
        if dt is None:
            continue
        age = now - dt
        if age <= timedelta(days=30):
            recent += 1
        elif age <= timedelta(days=60):
            prior += 1
    return round(recent / max(prior, min_denominator), 3)


def member_hash(item_ids: list[int]) -> str:
    return hashlib.sha1(",".join(str(i) for i in sorted(item_ids)).encode()).hexdigest()


def label_cluster(llm: BaseLLM, summaries: list[str], domain: str | None) -> dict[str, str]:
    user = json.dumps({"domain": domain, "summaries": summaries}, ensure_ascii=False)
    data = llm.complete_json(LABEL_SYSTEM, user, purpose="cluster_label", max_tokens=4000, retries=1)
    if not isinstance(data, dict):
        raise LLMError("label response is not an object")
    return {"label": str(data.get("label") or summaries[0])[:200],
            "canonical_summary": str(data.get("canonical_summary") or summaries[0])[:1000],
            "domain": str(data.get("domain") or domain or "general")[:100]}


def run_clustering(cfg: dict, conn: sqlite3.Connection, llm: BaseLLM | None) -> dict[str, Any]:
    ccfg = cfg.get("clustering", {})
    threshold = float(ccfg.get("distance_threshold", 0.35))
    sample_n = int(ccfg.get("label_sample_size", 10))
    label_min = int(ccfg.get("label_min_size", 2))
    min_denom = int(ccfg.get("min_growth_denominator", 3))
    rows = dbm.problems_for_clustering(conn)
    stats: dict[str, Any] = {"problems": len(rows), "clusters": 0, "labelled": 0, "label_failures": 0}
    if not rows:
        log.info("cluster: no classified problems yet")
        return stats
    run_id = dbm.start_run(conn, "cluster")
    texts = [r["problem_summary"] for r in rows]
    log.info("cluster: embedding %d summaries with %s", len(texts), ccfg.get("embedding_model"))
    vecs = embed(texts, ccfg.get("embedding_model", "sentence-transformers/all-MiniLM-L6-v2"))
    labels = cluster_labels(vecs, threshold)
    groups: dict[int, list[int]] = {}
    for i, lab in enumerate(labels):
        groups.setdefault(lab, []).append(i)
    dbm.reset_clusters(conn)
    now = now_utc()
    for lab in sorted(groups):
        members = [rows[i] for i in groups[lab]]
        dates = [m["created_at"] for m in members]
        parsed = sorted(d for d in (parse_dt(x) for x in dates) if d)
        domain = Counter(m["domain"] for m in members if m["domain"]).most_common(1)
        domain = domain[0][0] if domain else None
        sources = dict(Counter(m["source"] for m in members))
        # sample: highest pain first, then most recent
        sample = sorted(members, key=lambda m: (-(m["pain_score"] or 0), m["created_at"] or ""), reverse=False)
        summaries = [m["problem_summary"] for m in sample[:sample_n]]
        label, canonical = summaries[0], summaries[0]
        if llm is not None and len(members) >= label_min:
            try:
                info = label_cluster(llm, summaries, domain)
                label, canonical, domain = info["label"], info["canonical_summary"], info["domain"]
                stats["labelled"] += 1
            except LLMError as e:
                stats["label_failures"] += 1
                log.warning("cluster: labelling failed (%s); using member summary", e)
        cid = dbm.insert_cluster(conn, {
            "label": label, "canonical_summary": canonical, "domain": domain, "item_count": len(members),
            "first_seen": iso(parsed[0]) if parsed else None, "last_seen": iso(parsed[-1]) if parsed else None,
            "sources": sources, "growth_30d": growth_ratio(dates, now, min_denom),
            "member_hash": member_hash([m["item_id"] for m in members]),
        })
        dbm.assign_cluster(conn, cid, [m["id"] for m in members])
        stats["clusters"] += 1
        conn.commit()
    dbm.finish_run(conn, run_id, notes=str(stats))
    conn.commit()
    log.info("cluster: %d clusters from %d problems (threshold %.2f)", stats["clusters"], len(rows), threshold)
    return stats
