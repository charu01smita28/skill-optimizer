"""Tests for `propose_model_swap` — covers replace, insert, and skip cases."""
from __future__ import annotations

from skill_optimizer.domain.mutations import propose_model_swap
from skill_optimizer.domain.types import Finding


def _finding() -> Finding:
    return Finding(
        finding_id="f-001",
        detector_id="D004",
        skill_id="x",
        category="model_tier_overkill",
        observed_pattern="",
        evidence=(),
        estimated_cost_pct=-70.0,
        estimated_latency_pct=-30.0,
        quality_risk="low",
        occurrences=3,
    )


def test_proposes_replace_when_sonnet_declared() -> None:
    skill = "---\nname: x\nmodel: claude-sonnet-4-6\n---\n# body\n"
    proposal = propose_model_swap(_finding(), current_skill_text=skill)
    assert proposal is not None
    assert proposal.patch.before_text == "model: claude-sonnet-4-6"
    assert proposal.patch.after_text == "model: claude-haiku-4-5"


def test_proposes_insert_when_no_model_declared() -> None:
    skill = "---\nname: x\n---\n# body\n"
    proposal = propose_model_swap(_finding(), current_skill_text=skill)
    assert proposal is not None
    assert proposal.patch.before_text == ""
    assert proposal.patch.after_text == "model: claude-haiku-4-5"


def test_returns_none_when_target_already_declared() -> None:
    """Skill already declares Haiku — D004 only fired because cached baseline
    traces show Sonnet (pre-8.0a runtime-override anomaly)."""
    skill = "---\nname: x\nmodel: claude-haiku-4-5\n---\n# body\n"
    proposal = propose_model_swap(_finding(), current_skill_text=skill)
    assert proposal is None
