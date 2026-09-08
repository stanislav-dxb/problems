import json
from datetime import timedelta
from pathlib import Path

import numpy as np

from scout.cluster import cluster_labels, growth_ratio, member_hash
from scout.util import now_utc

FIXTURE = Path(__file__).parent / "fixtures" / "embeddings.json"


def _partition(labels):
    groups = {}
    for i, lab in enumerate(labels):
        groups.setdefault(lab, set()).add(i)
    return sorted(groups.values(), key=lambda s: (-len(s), min(s)))


def test_clustering_is_deterministic_on_fixture():
    fx = json.loads(FIXTURE.read_text())
    vecs = np.array(fx["vectors"], dtype=np.float32)
    first = cluster_labels(vecs, fx["distance_threshold"])
    for _ in range(3):
        assert cluster_labels(vecs, fx["distance_threshold"]) == first


def test_clustering_recovers_fixture_groups():
    fx = json.loads(FIXTURE.read_text())
    vecs = np.array(fx["vectors"], dtype=np.float32)
    labels = cluster_labels(vecs, fx["distance_threshold"])
    expected = {}
    for i, g in enumerate(fx["expected_groups"]):
        expected.setdefault(g, set()).add(i)
    assert _partition(labels) == sorted(expected.values(), key=lambda s: (-len(s), min(s)))
    # label 0 is the largest cluster; the outlier is alone
    assert labels.count(0) == 5 and min(labels.count(l) for l in set(labels)) == 1


def test_clustering_partition_is_order_invariant():
    fx = json.loads(FIXTURE.read_text())
    vecs = np.array(fx["vectors"], dtype=np.float32)
    perm = np.random.RandomState(1).permutation(len(vecs))
    a = _partition(cluster_labels(vecs, fx["distance_threshold"]))
    b = _partition(cluster_labels(vecs[perm], fx["distance_threshold"]))
    inv = {int(p): i for i, p in enumerate(perm)}
    b_mapped = sorted(({int(perm[j]) for j in s} for s in b), key=lambda s: (-len(s), min(s)))
    assert a == b_mapped


def test_clustering_edge_cases():
    assert cluster_labels(np.zeros((0, 4), dtype=np.float32), 0.35) == []
    assert cluster_labels(np.ones((1, 4), dtype=np.float32), 0.35) == [0]


def test_growth_ratio():
    now = now_utc()
    recent = [now - timedelta(days=d) for d in (1, 5, 10, 20, 29)]
    prior = [now - timedelta(days=d) for d in (35, 45)]
    # denominator floored at 3
    assert growth_ratio(recent + prior, now, min_denominator=3) == round(5 / 3, 3)
    assert growth_ratio(recent + prior, now, min_denominator=1) == 2.5
    assert growth_ratio([], now) == 0.0


def test_member_hash_is_order_independent():
    assert member_hash([3, 1, 2]) == member_hash([1, 2, 3]) != member_hash([1, 2])
