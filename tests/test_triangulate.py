"""Triangulation on a fixture set with a fake embedder: expected matches, geometric mean, reverse hypotheses."""
import hashlib

import numpy as np
import pytest

from scout import db as dbm
from scout.triangulate import geometric_mean, triangulate


def fake_embed(texts, model_name):
    vecs = []
    for t in texts:
        key = t.lower().split()[0]  # first word decides the topic
        seed = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
        v = np.random.RandomState(seed).normal(size=32)
        vecs.append(v / np.linalg.norm(v))
    return np.array(vecs, dtype=np.float32)


def _cluster(conn, label, n=5, growth=1.0):
    cid = dbm.insert_cluster(conn, {"label": label, "canonical_summary": label, "domain": "logistics", "item_count": n,
                                    "first_seen": "2026-09-01", "last_seen": "2026-09-08", "sources": {"hn": n},
                                    "growth_30d": growth, "member_hash": label})
    for i in range(n):
        dbm.insert_items(conn, [{"source": "hn", "source_id": f"{label}-{i}", "url": "u", "title": label, "body": "b",
                                 "author_handle": None, "language": "en", "created_at": "2026-09-05T00:00:00+00:00",
                                 "raw_json": None}])
        item_id = conn.execute("SELECT id FROM items WHERE source_id = ?", (f"{label}-{i}",)).fetchone()[0]
        dbm.upsert_problem(conn, {"item_id": item_id, "is_problem": 1, "problem_summary": label, "pain_score": 4,
                                  "classified_at": "t"})
        conn.execute("UPDATE problems SET cluster_id = ? WHERE item_id = ?", (cid, item_id))
    return cid


def test_geometric_mean():
    assert geometric_mean([1.0, 1.0, 1.0]) == 1.0
    assert geometric_mean([0.5, 0.0, 0.9]) == 0.0
    assert geometric_mean([0.25, 1.0, 0.25]) == pytest.approx(0.3969, abs=1e-3)


def test_triangulation_matches_and_reverse_hypotheses(conn, cfg, monkeypatch):
    monkeypatch.setattr("scout.triangulate.embed", fake_embed)
    customs = _cluster(conn, "customs clearance paperwork is manual")
    invoicing = _cluster(conn, "invoicing takes hours every month")
    rid = dbm.insert_report(conn, {"source": "t", "publisher": "World Bank", "title": "Trade report",
                                   "url": "https://example.org/wb", "confidence": 4, "published_at": "2026-08-01"})
    dbm.insert_claim(conn, {"report_id": rid, "source_chunk_id": "c1", "claim_type": "market_size",
                            "claim_summary": "customs brokerage market worth $12 billion", "numbers": [{"value": "$12 billion"}],
                            "geography": ["gulf", "india"], "confidence_in_source": 4})
    dbm.insert_claim(conn, {"report_id": rid, "source_chunk_id": "c2", "claim_type": "structural_gap",
                            "claim_summary": "warehousing is fragmented with no standard", "geography": ["india"],
                            "confidence_in_source": 4})  # no cluster starts with "warehousing" -> hypothesis
    aid = dbm.insert_article(conn, {"feed": "f", "kind": "regulatory", "title": "customs e-clearance mandate from 2027",
                                    "url": "https://example.org/n1", "published_at": "2026-09-07T00:00:00+00:00"})
    dbm.insert_catalyst(conn, {"article_id": aid, "is_catalyst": 1, "catalyst_type": "new_mandate",
                               "summary": "customs e-clearance mandate from 2027", "geography": ["gulf"],
                               "catalyst_strength": 4, "time_horizon": "1_3_years"})
    aid2 = dbm.insert_article(conn, {"feed": "f", "kind": "press", "title": "solar panel prices halved",
                                     "url": "https://example.org/n2", "published_at": "2026-09-07T00:00:00+00:00"})
    dbm.insert_catalyst(conn, {"article_id": aid2, "is_catalyst": 1, "catalyst_type": "cost_collapse",
                               "summary": "solar panel prices halved", "geography": ["china"], "catalyst_strength": 5})
    stats = triangulate(cfg, conn)
    assert stats["triangulated"] == 2 and stats["with_all_legs"] == 1 and stats["hypotheses"] == 2
    tri = dbm.triangulations_by_cluster(conn)
    t = tri[customs]
    assert t["market_evidence"] > 0 and t["timing_evidence"] > 0 and t["pain_evidence"] > 0
    expected = 100 * geometric_mean([t["pain_evidence"], t["market_evidence"], t["timing_evidence"]])
    assert t["triangulation_score"] == pytest.approx(expected, abs=0.11)
    assert "$12 billion" in t["market_size_source"] and "mandate" in t["why_now"]
    assert sorted(__import__("json").loads(t["geography"])) == ["gulf", "india"]
    t2 = tri[invoicing]
    assert t2["triangulation_score"] == 0 and t2["market_evidence"] == 0 and "weakest leg" in t2["evidence_gaps"]
    hyps = list(conn.execute("SELECT kind, summary, ask_whom FROM hypotheses ORDER BY kind"))
    assert [h["kind"] for h in hyps] == ["news", "report"]
    assert hyps[0]["summary"].startswith("solar") and hyps[1]["summary"].startswith("warehousing")
    assert all(h["ask_whom"] for h in hyps)
