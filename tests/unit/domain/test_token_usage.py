"""Unit tests for ``token_usage`` bucket sums + per-model cost math."""
from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock

from skill_optimizer.config.pricing import Pricing, load_pricing
from skill_optimizer.domain.token_usage import (
    TokenUsage,
    cost_at_model,
    trace_cost,
    trace_usage,
)
from skill_optimizer.domain.trace import Trace


REPO_ROOT = Path(__file__).resolve().parents[3]
PRICING_PATH = REPO_ROOT / "config" / "pricing.yaml"


@pytest.fixture(scope="module")
def pricing() -> Pricing:
    return load_pricing(PRICING_PATH)


def _msg(model: str, *, inp: int, out: int, cr: int, cc: int) -> AssistantMessage:
    return AssistantMessage(
        content=[TextBlock(text="x")],
        model=model,
        usage={
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_input_tokens": cr,
            "cache_creation_input_tokens": cc,
        },
    )


def _trace_of(*messages: AssistantMessage) -> Trace:
    return Trace(
        session_id="s",
        cwd="/tmp",
        initial_prompt="x",
        version="0.0.0",
        is_sidechain=False,
        messages=tuple(messages),
    )


def test_from_usage_dict_handles_missing_buckets() -> None:
    u = TokenUsage.from_usage_dict({"input_tokens": 5})
    assert u == TokenUsage(5, 0, 0, 0)
    assert TokenUsage.from_usage_dict(None) == TokenUsage.zero()


def test_addition_is_componentwise() -> None:
    a = TokenUsage(1, 2, 3, 4)
    b = TokenUsage(10, 20, 30, 40)
    assert a + b == TokenUsage(11, 22, 33, 44)


def test_cost_at_sonnet_matches_calculations_md(pricing: Pricing) -> None:
    # CALCULATIONS.md: input=3, output=235, cache_read=11896, cache_creation=6431
    # at Sonnet rates → $0.031219.
    usage = TokenUsage(3, 235, 11896, 6431)
    rates = pricing.rates_for("claude-sonnet-4-6")
    assert usage.cost_at(rates) == pytest.approx(0.031219, rel=1e-4)


def test_trace_cost_sums_per_message_at_each_model(pricing: Pricing) -> None:
    # Two assistant turns, different models — multi-model trace shape.
    haiku_msg = _msg("claude-haiku-4-5", inp=1000, out=1000, cr=0, cc=0)
    sonnet_msg = _msg("claude-sonnet-4-6", inp=1000, out=1000, cr=0, cc=0)

    haiku_cost = (1000 * 0.80 + 1000 * 4.00) / 1_000_000
    sonnet_cost = (1000 * 3.00 + 1000 * 15.00) / 1_000_000
    expected = haiku_cost + sonnet_cost

    assert trace_cost(_trace_of(haiku_msg, sonnet_msg), pricing) == pytest.approx(expected)


def test_trace_usage_sums_buckets_across_messages() -> None:
    a = _msg("claude-haiku-4-5", inp=1, out=2, cr=3, cc=4)
    b = _msg("claude-haiku-4-5", inp=10, out=20, cr=30, cc=40)
    assert trace_usage(_trace_of(a, b)) == TokenUsage(11, 22, 33, 44)


def test_cost_at_model_reprices_total_usage(pricing: Pricing) -> None:
    # D004 path: cost the same trace as if it ran entirely on Haiku.
    msg = _msg("claude-sonnet-4-6", inp=1000, out=1000, cr=10000, cc=2000)
    trace = _trace_of(msg)
    expected_haiku = (
        1000 * 0.80
        + 1000 * 4.00
        + 10000 * 0.08
        + 2000 * 1.00
    ) / 1_000_000
    assert cost_at_model(trace, pricing, "claude-haiku-4-5") == pytest.approx(expected_haiku)


def test_unknown_model_raises(pricing: Pricing) -> None:
    msg = _msg("not-a-real-model", inp=1, out=1, cr=0, cc=0)
    with pytest.raises(KeyError):
        trace_cost(_trace_of(msg), pricing)


def test_rates_for_strips_dated_suffix(pricing: Pricing) -> None:
    # SDK alias `claude-haiku-4-5` resolves to `claude-haiku-4-5-20251001` in
    # actual API calls; pricing.yaml keys the alias.
    aliased = pricing.rates_for("claude-haiku-4-5")
    dated = pricing.rates_for("claude-haiku-4-5-20251001")
    assert aliased is dated


def test_pricing_loader_has_three_models(pricing: Pricing) -> None:
    assert pricing.as_of == "2026-05-06"
    assert set(pricing.models) == {
        "claude-haiku-4-5",
        "claude-sonnet-4-6",
        "claude-opus-4-7",
    }
