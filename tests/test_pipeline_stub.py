"""End-to-end smoke test with the stub LLM and a fake embedder (no network, no model download)."""
import hashlib

import numpy as np

from scout import db as dbm
from scout.classify import classify
from scout.cluster import run_clustering
from scout.digest import build_digest
from scout.evaluate import evaluate
from scout.llm import StubLLM
from scout.sources.base import make_item


def fake_embed(texts, model_name):
    """Same-first-word texts get near-identical vectors; different ones are orthogonal-ish."""
    vecs = []
    for t in texts:
        key = t.lower().split()[1] if t.lower().startswith("[stub]") else t.lower().split()[0]
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
                               f"and it costs me hours ({i}).", "u", 1_757_000_000 - i * 86400, {}))
    for i in range(3):
        items.append(make_item("appstore", f"b{i}", f"https://example.com/b{i}", "Shipping labels",
                               "Shipping labels: is there a tool that prints labels for my store automatically? "
                               f"Manually copying addresses is a nightmare ({i}).", "v", 1_757_000_000 - i * 86400, {}))
    items.append(make_item("hn", "c0", "https://example.com/c0", "Show HN: my new app",
                           "We launched a new product today, it is great and fast and shiny and everyone loves it.",
                           "w", 1_757_000_000, {}))
    assert dbm.insert_items(conn, items) == (10, 0)
    llm = StubLLM(conn)
    cfg["evaluation"]["min_cluster_size"] = 3
    st = classify(cfg, conn, llm)
    assert st["items"] == 10 and st["problems"] == 9 and st["non_problems"] == 1 and st["failed"] == 0
    cs = run_clustering(cfg, conn, llm)
    assert cs["clusters"] == 2 and cs["problems"] == 9
    sizes = sorted(r[0] for r in conn.execute("SELECT item_count FROM clusters"))
    assert sizes == [3, 6]
    ev = evaluate(cfg, conn, llm)
    assert ev["evaluated"] == 2 and ev["failed"] == 0
    # re-clustering with identical membership reuses evaluations instead of re-calling the model
    run_clustering(cfg, conn, llm)
    ev2 = evaluate(cfg, conn, llm)
    assert ev2["reused"] == 2 and ev2["evaluated"] == 0
    md = build_digest(cfg, conn)
    assert "STUB MODE" in md and "## Top 2 problems" in md and "example.com/a0" in md
    assert "## Cross-market gaps" in md and "## Rising" in md
