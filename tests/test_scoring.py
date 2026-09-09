import pytest

from scout.config import DEFAULTS
from scout.score import COMPONENTS, compute_overall_score, normalise_growth

W = DEFAULTS["scoring"]["weights"]


def test_weights_cover_all_components_and_sum_to_one():
    assert set(W) == set(COMPONENTS)
    assert sum(W.values()) == pytest.approx(1.0)


def test_max_and_min_scores():
    assert compute_overall_score({k: 1.0 for k in COMPONENTS}, W) == 100.0
    assert compute_overall_score({k: 0.0 for k in COMPONENTS}, W) == 0.0
    assert compute_overall_score({}, W) == 0.0


def test_hand_computed_case():
    comps = {"volume": 1.0, "growth": 0.5, "sources": 0.0, "languages": 0.0, "pain": 0.5,
             "money": 1.0, "demand": 0.0, "workaround": 1.0}
    # 0.20*1 + 0.12*0.5 + 0.12*0.5 + 0.08*1 + 0.04*1 = 0.44 (triangulation absent = 0)
    assert compute_overall_score(comps, W) == 44.0


def test_components_are_clipped_and_weights_normalised():
    assert compute_overall_score({"volume": 5.0}, {"volume": 2.0, "growth": 2.0}) == 50.0


def test_growth_normalisation():
    assert normalise_growth(None) == 0.0
    assert normalise_growth(0.25) == 0.0
    assert normalise_growth(1.0) == 0.5
    assert normalise_growth(4.0) == 1.0
    assert normalise_growth(100.0) == 1.0
