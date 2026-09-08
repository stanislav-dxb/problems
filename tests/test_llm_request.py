import pytest

from scout.llm import DEFAULT_EFFORT, FALLBACK_BETA, build_request, estimate_cost, price_for


def test_request_shape_for_opus_5():
    req = build_request("claude-opus-5", "SYS", "USER", "classify", 16000)
    assert req["model"] == "claude-opus-5" and req["max_tokens"] == 16000
    assert req["thinking"] == {"type": "adaptive"}
    assert req["output_config"] == {"effort": "low"}
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"} and req["system"][0]["text"] == "SYS"
    assert req["messages"] == [{"role": "user", "content": "USER"}]
    assert req["fallbacks"] == "default" and req["betas"] == [FALLBACK_BETA]
    assert "temperature" not in req and "budget_tokens" not in str(req)


def test_effort_per_purpose_and_override():
    assert build_request("m", "s", "u", "evaluate", 1)["output_config"]["effort"] == DEFAULT_EFFORT["evaluate"]
    assert build_request("m", "s", "u", "evaluate", 1, effort={"evaluate": "xhigh"})["output_config"]["effort"] == "xhigh"
    assert build_request("m", "s", "u", "unknown_purpose", 1)["output_config"]["effort"] == "high"
    with pytest.raises(ValueError):
        build_request("m", "s", "u", "classify", 1, effort={"classify": "turbo"})


def test_fallbacks_can_be_disabled():
    req = build_request("claude-opus-5", "s", "u", "classify", 1, fallbacks="none")
    assert "fallbacks" not in req and "betas" not in req


def test_pricing():
    assert price_for("claude-opus-5") == (5.0, 25.0)
    assert estimate_cost("claude-opus-5", 1_000_000, 0) == 5.0
    assert estimate_cost("claude-opus-5", 0, 1_000_000) == 25.0
    assert estimate_cost("claude-opus-5", 0, 0, cache_read=1_000_000) == pytest.approx(0.5)
