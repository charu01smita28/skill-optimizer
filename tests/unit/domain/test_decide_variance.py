"""Tests for ``decide()`` variance-band policy: AUTO_APPLY / FLAG / REJECT."""
from __future__ import annotations

from skill_optimizer.domain.decision_policy import decide
from skill_optimizer.domain.types import (
    DecisionVerdict,
    Finding,
    Patch,
    Proposal,
    VerificationResult,
)


def _proposal() -> Proposal:
    finding = Finding(
        finding_id="f-1",
        detector_id="D004",
        skill_id="x",
        category="model_tier_overkill",
        observed_pattern="",
        evidence=(),
        estimated_cost_pct=-50.0,
        estimated_latency_pct=-30.0,
        quality_risk="low",
        occurrences=3,
    )
    patch = Patch(
        target_relative_path="SKILL.md",
        before_text="",
        after_text="model: claude-haiku-4-5",
        description="swap",
    )
    return Proposal(
        proposal_id="p-1",
        finding=finding,
        patch=patch,
        tier="1",
        mutation_type="model_swap",
    )


def _verification(
    *,
    cost_mean: float,
    cost_stddev: float = 0.0,
    equivalence: float = 1.0,
    verdict: str = "PASS",
    n: int = 3,
) -> VerificationResult:
    return VerificationResult(
        proposal_id="p-1",
        holdout_inputs=2,
        comparisons=(),
        equivalence_ratio=equivalence,
        cost_delta_pct=cost_mean,
        latency_delta_pct=-25.0,
        quality_delta=equivalence - 1.0,
        reliability_delta=0.0,
        verdict=verdict,
        cost_delta_stddev=cost_stddev,
        latency_delta_stddev=0.0,
        n_replays_per_input=n,
    )


def test_auto_apply_when_band_stays_on_win_side() -> None:
    """Mean -50% with stddev 5%: band [-55%, -45%], well below -10% threshold."""
    decision = decide(_proposal(), _verification(cost_mean=-50.0, cost_stddev=5.0))
    assert decision.decision == DecisionVerdict.AUTO_APPLY


def test_flag_when_band_crosses_threshold_but_mean_wins() -> None:
    """Mean -15% (passes -10%) but stddev 10% pushes high side to -5% (fails gate)."""
    decision = decide(_proposal(), _verification(cost_mean=-15.0, cost_stddev=10.0))
    assert decision.decision == DecisionVerdict.FLAG
    assert "FLAG" in decision.human_rationale
    assert "high side" in decision.human_rationale.lower()


def test_flag_when_band_sign_flips() -> None:
    """Mean -12% (passes gate) with stddev 20%: high side +8%, sign-flipped from win to regression."""
    decision = decide(_proposal(), _verification(cost_mean=-12.0, cost_stddev=20.0))
    assert decision.decision == DecisionVerdict.FLAG


def test_reject_when_mean_fails_gate() -> None:
    """Mean -5% (above -10% gate); REJECT regardless of stddev."""
    decision = decide(_proposal(), _verification(cost_mean=-5.0, cost_stddev=2.0))
    assert decision.decision == DecisionVerdict.REJECT
    assert "cost_delta=-5.0%" in decision.human_rationale


def test_reject_when_equivalence_fails() -> None:
    """Equivalence regression rejects even with strong cost win."""
    decision = decide(
        _proposal(),
        _verification(cost_mean=-80.0, cost_stddev=2.0, equivalence=0.5, verdict="FAIL"),
    )
    assert decision.decision == DecisionVerdict.REJECT
    assert "equivalence=0.50" in decision.human_rationale


def test_n1_backward_compat_zero_stddev_auto_applies() -> None:
    """N=1 fixtures (stddev=0) act like the original gate: mean alone decides."""
    decision = decide(
        _proposal(),
        _verification(cost_mean=-30.0, cost_stddev=0.0, n=1),
    )
    assert decision.decision == DecisionVerdict.AUTO_APPLY


def test_n1_backward_compat_zero_stddev_rejects() -> None:
    decision = decide(
        _proposal(),
        _verification(cost_mean=-5.0, cost_stddev=0.0, n=1),
    )
    assert decision.decision == DecisionVerdict.REJECT


def test_boundary_case_high_side_exactly_at_threshold_auto_applies() -> None:
    """Mean -15%, stddev 5%: high side exactly -10% — still clears the gate (≤ comparison)."""
    decision = decide(
        _proposal(),
        _verification(cost_mean=-15.0, cost_stddev=5.0),
    )
    assert decision.decision == DecisionVerdict.AUTO_APPLY


def test_rationale_includes_replay_count_and_variance() -> None:
    """AUTO_APPLY rationale surfaces N and the ± variance band."""
    decision = decide(
        _proposal(),
        _verification(cost_mean=-40.0, cost_stddev=3.5, n=3),
    )
    assert "N=3" in decision.human_rationale
    assert "± 3.5%" in decision.human_rationale
