"""Detector estimate tests for D001 — real cost math, no spike constants."""
from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ToolUseBlock

from skill_optimizer.config.pricing import Pricing, load_pricing
from skill_optimizer.domain.detectors import detect_redundant_lookups
from skill_optimizer.domain.trace import Trace
from skill_optimizer.ports.trace_store import CapturedRun


_PRICING_PATH = Path(__file__).resolve().parents[3] / "config" / "pricing.yaml"


@pytest.fixture(scope="module")
def pricing() -> Pricing:
    return load_pricing(_PRICING_PATH)


def _read_msg(file_path: str, *, idx: int, model: str = "claude-haiku-4-5",
              inp: int = 3, out: int = 80, cr: int = 1000, cc: int = 200) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolUseBlock(id=f"tu-{idx}", name="Read", input={"file_path": file_path})],
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
        input_filename="policy_001",
        input_text="x",
        output={"fully_compliant": False},
        trace=trace,
        elapsed_s=1.0,
    )


def test_estimate_savings_match_dropped_turn_share(pricing: Pricing) -> None:
    """Three identical Reads of /tmp/x; the helper should predict ~-66% cost
    delta (2 of 3 turns dropped, all turns equal-cost)."""
    runs = [
        _run(i, _trace([
            _read_msg("/tmp/x", idx=0),
            _read_msg("/tmp/x", idx=1),
            _read_msg("/tmp/x", idx=2),
        ]))
        for i in range(3)
    ]
    findings = detect_redundant_lookups(skill_id="policy_compliance_auditor", runs=runs, pricing=pricing)
    assert len(findings) == 1
    f = findings[0]
    # 2 redundant turns out of 3 equal-cost turns → -66.67%
    assert f.estimated_cost_pct == pytest.approx(-200.0 / 3, rel=1e-3)
    # Output tokens proxy for latency: same shape → same ratio
    assert f.estimated_latency_pct == pytest.approx(-200.0 / 3, rel=1e-3)


def test_estimate_is_zero_when_no_redundant_pattern_fires(pricing: Pricing) -> None:
    """Single Read per trace → no D001 finding → no estimate to compute."""
    runs = [_run(i, _trace([_read_msg("/tmp/x", idx=0)])) for i in range(3)]
    assert detect_redundant_lookups(skill_id="x", runs=runs, pricing=pricing) == []


def test_estimate_falls_back_to_zero_for_unknown_model(pricing: Pricing) -> None:
    """Unknown model → that turn is silently skipped; no crash."""
    runs = []
    for i in range(3):
        msg_a = _read_msg("/tmp/x", idx=0, model="not-a-real-model")
        msg_b = _read_msg("/tmp/x", idx=1, model="not-a-real-model")
        runs.append(_run(i, _trace([msg_a, msg_b])))
    findings = detect_redundant_lookups(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    # baseline_cost stayed 0 → division skipped → cost_pct fell back to 0.0
    assert findings[0].estimated_cost_pct == 0.0


def test_estimate_replaces_spike_constant(pricing: Pricing) -> None:
    """Real math should not return the old _REDUNDANT_LOOKUP_COST_PCT = -3.0."""
    runs = [
        _run(i, _trace([_read_msg("/tmp/x", idx=0), _read_msg("/tmp/x", idx=1)]))
        for i in range(3)
    ]
    findings = detect_redundant_lookups(skill_id="x", runs=runs, pricing=pricing)
    assert findings[0].estimated_cost_pct != -3.0
