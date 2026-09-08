import pytest

from scout.config import DEFAULTS
from scout.evaluate import compute_overall_score, normalise_growth

W = DEFAULTS["evaluation"]["weights"]


def test_weights_sum_to_one():
    assert sum(W.values()) == pytest.approx(1.0)


def test_max_and_min_scores():
    top = {"path_to_1b_score": 5, "monopoly_potential": 5, "location_independent": 5, "capital_light": 5,
           "measurable_90d": 5}
    assert compute_overall_score(top, 4.0, W) == 100.0
    bottom = {k: 1 for k in top}
    assert compute_overall_score(bottom, 0.25, W) == 0.0


def test_hand_computed_case():
    s = {"path_to_1b_score": 5, "monopoly_potential": 3, "location_independent": 5, "capital_light": 1,
         "measurable_90d": 3}
    # 0.35*1 + 0.25*0.5 + 0.15*1 + 0.10*0 + 0.05*0.5 + 0.10*0.5(flat growth) = 0.70
    assert compute_overall_score(s, 1.0, W) == 70.0


def test_missing_scores_count_as_zero_and_growth_none_is_zero():
    assert compute_overall_score({}, None, W) == 0.0
    assert compute_overall_score({"path_to_1b_score": "5"}, None, W) == 35.0


def test_growth_normalisation():
    assert normalise_growth(None) == 0.0
    assert normalise_growth(0.25) == 0.0
    assert normalise_growth(1.0) == 0.5
    assert normalise_growth(4.0) == 1.0
    assert normalise_growth(100.0) == 1.0
