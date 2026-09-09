"""End-to-end run with a fake embedder (no network, no model download)."""
import hashlib

import numpy as np

from scout import db as dbm
from scout.classify import classify
from scout.cluster import run_clustering
from scout.digest import build_digest
from scout.score import score
from scout.sources.base import make_item


def fake_embed(texts, model_name):
    """Texts sharing their first word get near-identical vectors; others are far apart."""
    vecs = []
    for t in texts:
        key = t.lower().split()[0]
        seed = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
        v = np.random.RandomState(seed).normal(size=32)
        v += np.random.RandomState(seed + 1).normal(size=32) * 0.01
        vecs.append(v / np.linalg.norm(v))
    return np.array(vecs, dtype=np.float32)


def test_pipeline_end_to_end(conn, cfg, monkeypatch):
    monkeypatch.setattr("scout.cluster.embed", fake_embed)
    items = []
    for i in range(6):
        items.append(make_item("hn", f"a{i}", f"https://example.com/a{i}", "Invoicing pain",
                               "Invoicing is a pain in the neck, I do it manually in a spreadsheet every month "
                               f"and it costs me hours and $50 ({i}).", "u", 1_757_000_000 - i * 86400, {}))
    for i in range(3):
        items.append(make_item("appstore", f"b{i}", f"https://example.com/b{i}", "ShipApp: labels",
                               "Shipping labels: is there a tool that prints labels for my store automatically? "
                               f"Manually copying addresses is a nightmare ({i}).", "v", 1_757_000_000 - i * 86400,
                               {"rating": 1, "category": "ecommerce"}))
    items.append(make_item("hn", "c0", "https://example.com/c0", "Show HN: my new app",
                           "We just launched a new product today, it is great and fast and everyone loves it.",
                           "w", 1_757_000_000, {}))
    assert dbm.insert_items(conn, items) == (10, 0)
    st = classify(cfg, conn)
    assert (st["items"], st["problems"], st["non_problems"], st["backend"]) == (10, 9, 1, "rules")
    cs = run_clustering(cfg, conn)
    assert cs == {"problems": 9, "clusters": 2}
    sizes = sorted(r[0] for r in conn.execute("SELECT item_count FROM clusters"))
    assert sizes == [3, 6]
    labels = {r[0] for r in conn.execute("SELECT label FROM clusters")}
    assert any("ShipApp" in l for l in labels)  # titled source uses the medoid title
    sc = score(cfg, conn)
    assert sc == {"clusters": 2, "scored": 2}
    rows = dbm.scores_by_cluster(conn)
    assert all(0 <= r["overall_score"] <= 100 for r in rows.values())
    md = build_digest(cfg, conn)
    assert "## Top 2 problems" in md and "example.com/a0" in md and "Excel" not in md
    assert "## Cross-market gaps" in md and "## Rising" in md
    # re-running classify is a no-op; --reclassify redoes everything
    assert classify(cfg, conn)["items"] == 0
    assert classify(cfg, conn, reclassify=True)["items"] == 10
