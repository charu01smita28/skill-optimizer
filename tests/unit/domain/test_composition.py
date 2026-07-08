"""Composition: the dispatch loop stages every AUTO_APPLY patch onto a scratch copy
in decision order; ``compose_optimized_skill`` publishes that copy + the manifest.
"""
from __future__ import annotations

import json
from pathlib import Path

from skill_optimizer.domain.report import compose_optimized_skill, stage_accepted_patches
from skill_optimizer.domain.types import (
    DecisionVerdict,
    Finding,
    OptimizationDecision,
    Patch,
    Proposal,
    VerificationResult,
)


_BASELINE_SKILL = """---
name: ticket-router
model: claude-sonnet-4-6
---

# Ticket Router

Read the ticket and route it.
"""


def _patch_model_swap() -> Patch:
    return Patch(
        target_relative_path="SKILL.md",
        before_text="model: claude-sonnet-4-6",
        after_text="model: claude-haiku-4-5",
        description="Downgrade model tier",
    )


def _patch_preload() -> Patch:
    return Patch(
        target_relative_path="SKILL.md",
        before_text="# Ticket Router",
        after_text="> Optimizer note: do not re-fetch.\n\n# Ticket Router",
        description="Insert preload-file directive",
    )


def _patch_full_file(before: str, after: str, description: str) -> Patch:
    return Patch(
        target_relative_path="SKILL.md",
        before_text=before,
        after_text=after,
        description=description,
        full_file=True,
    )


def _decision(
    decision_id: str,
    patch: Patch,
    mutation_type: str,
    detector_id: str = "D004",
    verdict: DecisionVerdict = DecisionVerdict.AUTO_APPLY,
) -> OptimizationDecision:
    finding = Finding(
        finding_id=f"{decision_id}-f",
        detector_id=detector_id,
        skill_id="ticket_router",
        category="x",
        observed_pattern="x",
        evidence=(),
        estimated_cost_pct=-30.0,
        estimated_latency_pct=-25.0,
        quality_risk="low",
        occurrences=3,
    )
    proposal = Proposal(
        proposal_id=f"{decision_id}-p",
        finding=finding,
        patch=patch,
        tier="2" if patch.full_file else "1",
        mutation_type=mutation_type,
    )
    verification = VerificationResult(
        proposal_id=proposal.proposal_id,
        holdout_inputs=2,
        comparisons=(),
        equivalence_ratio=1.0,
        cost_delta_pct=-30.0,
        latency_delta_pct=-25.0,
        quality_delta=0.0,
        reliability_delta=0.0,
        verdict="PASS",
    )
    return OptimizationDecision(
        decision_id=decision_id,
        proposal=proposal,
        verification=verification,
        decision=verdict,
        human_rationale="ok",
        decided_at="2026-05-07T00:00:00Z",
    )


def _make_skill(tmp_path: Path) -> Path:
    skill_dir = tmp_path / "ticket_router"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_BASELINE_SKILL)
    return skill_dir


def _stage(tmp_path: Path, skill_dir: Path, decisions: list[OptimizationDecision]) -> Path:
    return stage_accepted_patches(skill_dir, decisions, tmp_path / "stage" / skill_dir.name)


def test_returns_none_when_no_auto_apply(tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path)
    decisions = [_decision("d1", _patch_model_swap(), "model_swap", verdict=DecisionVerdict.REJECT)]
    staged = _stage(tmp_path, skill_dir, decisions)
    assert compose_optimized_skill(
        staged, decisions, tmp_path / "runs", original_skill_dir=skill_dir,
    ) is None


def test_stacks_two_auto_apply_patches(tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path)
    decisions = [
        _decision("d1", _patch_model_swap(), "model_swap"),
        _decision("d2", _patch_preload(), "preload_file", detector_id="D001"),
    ]
    staged = _stage(tmp_path, skill_dir, decisions)
    optimized = compose_optimized_skill(
        staged, decisions, tmp_path / "runs", original_skill_dir=skill_dir,
    )
    assert optimized is not None
    text = (optimized / "SKILL.md").read_text()
    assert "model: claude-haiku-4-5" in text
    assert "model: claude-sonnet-4-6" not in text
    assert "Optimizer note" in text


def test_cumulative_full_file_rewrites_all_survive(tmp_path: Path) -> None:
    """Two stacked full-file rewrites where the second was built against the
    first's output: the composed skill must carry BOTH changes. The Day-11 bug
    was that only the last full-file write landed on disk.
    """
    skill_dir = _make_skill(tmp_path)
    rewrite_1 = _BASELINE_SKILL.replace(
        "Read the ticket and route it.",
        "Read the ticket and route it.\n\n## Tool Usage Guidance\n\nRead before Write.",
    )
    rewrite_2 = rewrite_1.replace(
        "# Ticket Router\n",
        "# Ticket Router\n\n## Environment\n\nAssume deps are pre-installed.\n",
    )
    decisions = [
        _decision("d1", _patch_full_file(_BASELINE_SKILL, rewrite_1, "add tool guidance"),
                  "tool_guidance_rewrite", detector_id="D003"),
        _decision("d2", _patch_full_file(rewrite_1, rewrite_2, "add environment section"),
                  "cache_strategy_rewrite", detector_id="D006"),
    ]
    staged = _stage(tmp_path, skill_dir, decisions)
    optimized = compose_optimized_skill(
        staged, decisions, tmp_path / "runs", original_skill_dir=skill_dir,
    )
    assert optimized is not None
    text = (optimized / "SKILL.md").read_text()
    assert "## Tool Usage Guidance" in text   # D003's change survived
    assert "## Environment" in text            # D006's change survived (was clobbered before)
    assert text == rewrite_2


def test_mixed_tier1_and_full_file_rewrite_compose(tmp_path: Path) -> None:
    """A Tier-1 surgical patch (model swap) and a Tier-2 full-file rewrite stack
    when the rewrite was built against the surgically-patched text.
    """
    skill_dir = _make_skill(tmp_path)
    after_model_swap = _BASELINE_SKILL.replace("claude-sonnet-4-6", "claude-haiku-4-5")
    rewrite = after_model_swap.replace(
        "Read the ticket and route it.",
        "Read the ticket and route it.\n\n## Tool Usage Guidance\n\nRead before Write.",
    )
    decisions = [
        _decision("d1", _patch_model_swap(), "model_swap"),
        _decision("d2", _patch_full_file(after_model_swap, rewrite, "add guidance"),
                  "tool_guidance_rewrite", detector_id="D003"),
    ]
    staged = _stage(tmp_path, skill_dir, decisions)
    optimized = compose_optimized_skill(
        staged, decisions, tmp_path / "runs", original_skill_dir=skill_dir,
    )
    assert optimized is not None
    text = (optimized / "SKILL.md").read_text()
    assert "model: claude-haiku-4-5" in text
    assert "## Tool Usage Guidance" in text


def test_writes_applied_patches_manifest(tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path)
    decisions = [
        _decision("d1", _patch_model_swap(), "model_swap"),
        _decision("d2", _patch_preload(), "preload_file", detector_id="D001"),
    ]
    staged = _stage(tmp_path, skill_dir, decisions)
    optimized = compose_optimized_skill(
        staged, decisions, tmp_path / "runs", original_skill_dir=skill_dir,
    )
    assert optimized is not None
    manifest_path = optimized.parent / "applied_patches.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert [m["mutation_type"] for m in manifest] == ["model_swap", "preload_file"]


def test_skips_non_auto_apply_decisions(tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path)
    decisions = [
        _decision("d1", _patch_model_swap(), "model_swap"),
        _decision("d2", _patch_preload(), "preload_file", detector_id="D001",
                  verdict=DecisionVerdict.REJECT),
    ]
    staged = _stage(tmp_path, skill_dir, decisions)
    optimized = compose_optimized_skill(
        staged, decisions, tmp_path / "runs", original_skill_dir=skill_dir,
    )
    assert optimized is not None
    text = (optimized / "SKILL.md").read_text()
    assert "model: claude-haiku-4-5" in text
    # Rejected preload patch must NOT appear in the composed skill.
    assert "Optimizer note" not in text


def test_idempotent_when_run_twice(tmp_path: Path) -> None:
    """Re-publishing into the same run_dir replaces, not appends."""
    skill_dir = _make_skill(tmp_path)
    decisions = [_decision("d1", _patch_model_swap(), "model_swap")]
    staged = _stage(tmp_path, skill_dir, decisions)
    run_dir = tmp_path / "runs"
    compose_optimized_skill(staged, decisions, run_dir, original_skill_dir=skill_dir)
    optimized = compose_optimized_skill(staged, decisions, run_dir, original_skill_dir=skill_dir)
    assert optimized is not None
    assert (optimized / "SKILL.md").read_text().count("model: claude-haiku-4-5") == 1


# ---------- Patch.new_files (a mutation can create files, e.g. helper_extract) ----------

_HELPER_CODE = '''"""Auto-extracted helper."""


def validate_invoice(invoice: dict) -> dict:
    subtotal = round(sum(li["quantity"] * li["unit_price"] for li in invoice["line_items"]), 2)
    return {"subtotal": subtotal}
'''


def _patch_helper_extract(skill_text_before: str) -> Patch:
    """helper_extract-shaped: full-file SKILL.md rewrite + a new helper.py."""
    return Patch(
        target_relative_path="SKILL.md",
        before_text=skill_text_before,
        after_text=skill_text_before.replace(
            "Read the ticket and route it.", "Run `python helper.py` and emit its output."
        ),
        description="Extract validate_invoice into helper.py",
        full_file=True,
        new_files={"helper.py": _HELPER_CODE},
    )


def test_patch_writes_new_file_alongside_edit(tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path)
    patch = _patch_helper_extract(_BASELINE_SKILL)
    decisions = [_decision("d1", patch, "helper_extract", detector_id="D012")]
    staged = _stage(tmp_path, skill_dir, decisions)
    optimized = compose_optimized_skill(
        staged, decisions, tmp_path / "runs", original_skill_dir=skill_dir,
    )
    assert optimized is not None
    assert (optimized / "helper.py").read_text() == _HELPER_CODE
    assert "python helper.py" in (optimized / "SKILL.md").read_text()


def test_new_file_survives_stacking_with_earlier_patch(tmp_path: Path) -> None:
    """Model swap then helper_extract built against the swapped text: the composed
    skill carries the swap, the rewritten SKILL.md, and helper.py.
    """
    skill_dir = _make_skill(tmp_path)
    after_swap = _BASELINE_SKILL.replace("claude-sonnet-4-6", "claude-haiku-4-5")
    decisions = [
        _decision("d1", _patch_model_swap(), "model_swap"),
        _decision("d2", _patch_helper_extract(after_swap), "helper_extract", detector_id="D012"),
    ]
    staged = _stage(tmp_path, skill_dir, decisions)
    optimized = compose_optimized_skill(
        staged, decisions, tmp_path / "runs", original_skill_dir=skill_dir,
    )
    assert optimized is not None
    text = (optimized / "SKILL.md").read_text()
    assert "model: claude-haiku-4-5" in text
    assert "python helper.py" in text
    assert (optimized / "helper.py").read_text() == _HELPER_CODE


def test_compose_prunes_replay_side_effect_files(tmp_path: Path) -> None:
    """Replay side-effects in the staged dir must not leak through to the optimized dir."""
    skill_dir = _make_skill(tmp_path)
    patch = _patch_helper_extract(_BASELINE_SKILL)
    decisions = [_decision("d1", patch, "helper_extract", detector_id="D012")]
    staged = _stage(tmp_path, skill_dir, decisions)
    # Simulate the verifier's replay side effects: model writes random .py files
    # and an output.json into the staged dir that aren't in any patch.
    (staged / "validator.py").write_text("def validate_invoice(x): return x\n")
    (staged / "validate.py").write_text("# throwaway\n")
    (staged / "output.json").write_text('{"left": "over"}\n')
    (staged / "junk").mkdir()
    (staged / "junk" / "scratch.txt").write_text("noise\n")

    optimized = compose_optimized_skill(
        staged, decisions, tmp_path / "runs", original_skill_dir=skill_dir,
    )
    assert optimized is not None
    # Legit files survive.
    assert (optimized / "SKILL.md").exists()
    assert (optimized / "helper.py").read_text() == _HELPER_CODE
    # Replay side effects are gone.
    assert not (optimized / "validator.py").exists()
    assert not (optimized / "validate.py").exists()
    assert not (optimized / "output.json").exists()
    assert not (optimized / "junk").exists()  # empty dir pruned too


def test_apply_to_writes_new_files_directly(tmp_path: Path) -> None:
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# x\n")
    Patch(
        target_relative_path="SKILL.md",
        before_text="# x",
        after_text="# y",
        description="d",
        new_files={"helper.py": "print('hi')\n", "lib/util.py": "X = 1\n"},
    ).apply_to(skill_dir)
    assert (skill_dir / "SKILL.md").read_text() == "# y\n"
    assert (skill_dir / "helper.py").read_text() == "print('hi')\n"
    assert (skill_dir / "lib" / "util.py").read_text() == "X = 1\n"


def test_patch_to_json_includes_new_files() -> None:
    patch = Patch(
        target_relative_path="SKILL.md",
        before_text="a",
        after_text="b",
        description="d",
        new_files={"helper.py": "code"},
    )
    assert patch.to_json()["new_files"] == {"helper.py": "code"}
