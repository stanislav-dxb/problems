import pytest

from scout.classify import parse_classification
from scout.llm import StubLLM, extract_json


def test_extract_plain_array():
    assert extract_json('[{"idx": 0}]') == [{"idx": 0}]


def test_extract_from_markdown_fence():
    text = "Here you go:\n```json\n[{\"idx\": 0, \"is_problem\": true}]\n```\nThanks!"
    assert extract_json(text) == [{"idx": 0, "is_problem": True}]


def test_extract_with_leading_prose_and_trailing_text():
    text = 'Sure. {"label": "x", "canonical_summary": "y"} That is all.'
    assert extract_json(text) == {"label": "x", "canonical_summary": "y"}


def test_extract_raises_on_garbage():
    with pytest.raises(ValueError):
        extract_json("no json here")
    with pytest.raises(ValueError):
        extract_json("")


def test_parse_classification_normalises_types():
    payload = [{"idx": "0", "is_problem": "true", "domain": "construction", "who_has_it": "contractors",
                "problem_summary": "Cannot track change orders.", "pain_score": "7",
                "money_mentioned": 1, "workaround_described": "no", "solution_requested": True,
                "existing_solutions_named": "Procore"},
               {"idx": 1, "is_problem": False, "problem_summary": "ignored"}]
    out = parse_classification(payload, [0, 1])
    assert out[0]["is_problem"] == 1 and out[0]["pain_score"] == 5
    assert out[0]["money_mentioned"] == 1 and out[0]["workaround_described"] == 0
    assert out[0]["existing_solutions_named"] == ["Procore"]
    assert out[1]["is_problem"] == 0 and out[1]["problem_summary"] is None


def test_parse_classification_problem_without_summary_is_not_a_problem():
    out = parse_classification([{"idx": 0, "is_problem": True, "problem_summary": ""}], [0])
    assert out[0]["is_problem"] == 0


def test_parse_classification_positional_fallback_and_wrapper():
    payload = {"results": [{"is_problem": False}, {"is_problem": True, "problem_summary": "s"}]}
    out = parse_classification(payload, [10, 11])
    assert set(out) == {10, 11} and out[11]["is_problem"] == 1


def test_parse_classification_ignores_unknown_and_duplicate_idx():
    out = parse_classification([{"idx": 5}, {"idx": 0, "is_problem": False}, {"idx": 0, "is_problem": True}], [0])
    assert list(out) == [0] and out[0]["is_problem"] == 0


def test_parse_classification_rejects_non_array():
    with pytest.raises(ValueError):
        parse_classification("nope", [0])


def test_complete_json_retries_once_then_fails():
    class Flaky(StubLLM):
        calls = 0

        def complete(self, system, user, purpose, max_tokens=16000):
            Flaky.calls += 1
            return "not json"

    from scout.llm import LLMError
    with pytest.raises(LLMError):
        Flaky().complete_json("s", "u", purpose="classify", retries=1)
    assert Flaky.calls == 2
