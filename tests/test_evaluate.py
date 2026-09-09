import json

import pytest

from scout import db as dbm
from scout import llm
from scout.config import DEFAULTS
from scout.evaluate import _reusable, compute_overall_score, evaluate

W = DEFAULTS["evaluate"]["weights"]


def test_weights_sum_and_bounds():
    assert sum(W.values()) == pytest.approx(1.0)
    top = {"path_to_1b_score": 5, "monopoly_potential": 5, "location_independent": 5, "capital_light": 5, "measurable_90d": 5}
    assert compute_overall_score(top, 4.0, 100.0, W) == 100.0
    assert compute_overall_score({k: 1 for k in top}, 0.25, 0.0, W) == 0.0


def test_hand_computed_case():
    s = {"path_to_1b_score": 5, "monopoly_potential": 3, "location_independent": 5, "capital_light": 1, "measurable_90d": 3}
    # 0.30*1 + 0.20*0.5 + 0.20*0.5 (triangulation 50) + 0.12*1 + 0 + 0.05*0.5 + 0.05*0.5 (flat growth) = 0.67
    assert compute_overall_score(s, 1.0, 50.0, W) == 67.0


def test_reuse_exact_and_overlap(conn):
    prior = [dict(member_hash="h1", member_item_ids=json.dumps([1, 2, 3, 4, 5]), id=1)]
    rows = []
    for p in prior:
        conn.execute("INSERT INTO evaluations (cluster_id, member_hash, member_item_ids, evaluated_at) VALUES (9, ?, ?, 't')",
                     (p["member_hash"], p["member_item_ids"]))
    prior_rows = list(conn.execute("SELECT * FROM evaluations"))
    cid = dbm.insert_cluster(conn, {"label": "x", "canonical_summary": "x", "domain": None, "item_count": 5, "sources": {},
                                    "growth_30d": 1.0, "member_hash": "h1"})
    c = conn.execute("SELECT * FROM clusters WHERE id = ?", (cid,)).fetchone()
    assert _reusable(prior_rows, c, [1, 2, 3, 4, 5], 0.2) is not None            # exact hash
    c2 = dict(c); c2["member_hash"] = "other"
    class R(dict):
        def __getitem__(self, k): return dict.__getitem__(self, k)
    assert _reusable(prior_rows, R(c2), [1, 2, 3, 4, 5, 6], 0.2) is None          # 83% overlap but grew exactly 20%: re-evaluate
    assert _reusable(prior_rows, R(c2), [1, 2, 3, 4, 5, 6], 0.25) is not None     # under the 25% threshold: reuse
    assert _reusable(prior_rows, R(c2), [1, 2, 3, 4, 5, 6, 7, 8], 0.2) is None    # grew 60%
    assert _reusable(prior_rows, R(c2), [1, 2, 9, 10, 11], 0.2) is None           # 25% overlap


def test_evaluate_with_mocked_model(conn, cfg, monkeypatch):
    cfg["llm"] = {"backend": "claude_code", "model": "sonnet", "evaluate_model": "opus"}
    llm.configure(cfg, conn)
    cid = dbm.insert_cluster(conn, {"label": "invoicing", "canonical_summary": "invoicing takes hours", "domain": "smb",
                                    "item_count": 3, "sources": {"hn": 3}, "growth_30d": 2.0, "member_hash": "mh"})
    for i in range(3):
        dbm.insert_items(conn, [{"source": "hn", "source_id": f"e{i}", "url": "u", "title": "t", "body": "invoicing hurts",
                                 "author_handle": None, "language": "en", "created_at": "2026-09-05T00:00:00+00:00", "raw_json": None}])
        iid = conn.execute("SELECT id FROM items WHERE source_id = ?", (f"e{i}",)).fetchone()[0]
        dbm.upsert_problem(conn, {"item_id": iid, "is_problem": 1, "problem_summary": "invoicing hurts", "pain_score": 4, "classified_at": "t"})
        conn.execute("UPDATE problems SET cluster_id = ? WHERE item_id = ?", (cid, iid))
    reply = {"market_size_estimate": "$2B", "market_size_reasoning": "1M x $2k", "market_size_source": "none attached",
             "monopoly_potential": 3, "location_independent": 5, "runs_without_founder": 4, "capital_light": 4,
             "measurable_90d": 5, "path_to_1b": "para", "path_to_1b_score": 3, "why_now": "no catalysts attached",
             "what_would_kill_it": "incumbents", "quickest_test": "landing page", "evidence_gaps": "market", "corridor_advantage": ""}
    calls = []

    def fake_complete(system, user, model=None, stage=None, items=None):
        calls.append((model, stage))
        return "```json\n" + json.dumps(reply) + "\n```"

    monkeypatch.setattr(llm, "complete", fake_complete)
    st = evaluate(cfg, conn)
    assert st["evaluated"] == 1 and st["failed"] == 0 and calls == [("opus", "evaluate")]
    e = dbm.latest_evaluations(conn)[cid]
    assert e["path_to_1b_score"] == 3 and e["model"] == "opus" and 0 < e["overall_score"] <= 100
    # second run: nothing to do; --force re-evaluates
    assert evaluate(cfg, conn)["candidates"] == 0
    assert evaluate(cfg, conn, force=True)["evaluated"] == 1 and len(calls) == 2
