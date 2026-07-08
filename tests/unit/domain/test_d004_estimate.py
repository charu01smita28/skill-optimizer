"""Detector estimate tests for D004 — Sonnet→Haiku repricing per-trace."""
from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock

from skill_optimizer.config.pricing import Pricing, load_pricing
from skill_optimizer.domain.detectors import detect_model_tier_overkill
from skill_optimizer.domain.trace import Trace
from skill_optimizer.ports.trace_store import CapturedRun


_PRICING_PATH = Path(__file__).resolve().parents[3] / "config" / "pricing.yaml"


@pytest.fixture(scope="module")
def pricing() -> Pricing:
    return load_pricing(_PRICING_PATH)


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


def _trace(messages: list[AssistantMessage]) -> Trace:
    return Trace(
        session_id="s",
        cwd="/tmp",
        initial_prompt="x",
        version="0.0.0",
        is_sidechain=False,
        messages=tuple(messages),
    )


def _run(run_id: int, trace: Trace) -> CapturedRun:
    return CapturedRun(
        run_id=run_id,
        input_filename=f"x_{run_id}.txt",
        input_text="x",
        output={"requires_senior_review": True},
        trace=trace,
        elapsed_s=1.0,
    )


def test_sonnet_to_haiku_repricing_is_negative(pricing: Pricing) -> None:
    """Pure Sonnet trace → repricing at Haiku must be a savings (cost_pct < 0)."""
    runs = [
        _run(i, _trace([_msg("claude-sonnet-4-6", inp=1000, out=1000, cr=10000, cc=2000)]))
        for i in range(3)
    ]
    findings = detect_model_tier_overkill(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    cost_pct = findings[0].estimated_cost_pct
    assert cost_pct < 0
    # Hand-calc: baseline (Sonnet) = (1000*3 + 1000*15 + 10000*0.30 + 2000*3.75)/M = $0.0285
    #            haiku             = (1000*0.80 + 1000*4 + 10000*0.08 + 2000*1)/M = $0.0076
    #            delta             = (0.0076 - 0.0285) / 0.0285 = -73.33%
    assert cost_pct == pytest.approx(-73.33, abs=0.1)


def test_estimate_replaces_spike_constant(pricing: Pricing) -> None:
    """Real math should not collapse to the old _SONNET_TO_HAIKU_COST_PCT = -78.0."""
    runs = [
        _run(i, _trace([_msg("claude-sonnet-4-6", inp=1000, out=1000, cr=10000, cc=2000)]))
        for i in range(3)
    ]
    findings = detect_model_tier_overkill(skill_id="x", runs=runs, pricing=pricing)
    assert findings[0].estimated_cost_pct != -78.0


def test_no_finding_when_traces_already_use_small_tier(pricing: Pricing) -> None:
    """All traces on Haiku — no downgrade target → no finding."""
    runs = [
        _run(i, _trace([_msg("claude-haiku-4-5", inp=1000, out=1000, cr=0, cc=0)]))
        for i in range(3)
    ]
    assert detect_model_tier_overkill(skill_id="x", runs=runs, pricing=pricing) == []
