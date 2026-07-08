"""Tests for `propose_preload_file` — single-file, multi-file, basename, fallback."""
from __future__ import annotations

from skill_optimizer.domain.mutations import propose_preload_file
from skill_optimizer.domain.types import Finding


_SKILL = "---\nname: x\n---\n\n# My Skill\n\nbody\n"


def _pattern_evidence(*, tool_name: str, input_dict: dict, occurrences: int = 5) -> dict:
    return {
        "tool_name": tool_name,
        "input": input_dict,
        "input_summary": str(input_dict),
        "trace_refs": ["run_001.jsonl"],
        "occurrences": occurrences,
        "estimated_cost_pct": -50.0,
        "estimated_latency_pct": -30.0,
    }


def _finding(*evidence: dict, occurrences: int = 5) -> Finding:
    return Finding(
        finding_id="f-001",
        detector_id="D001",
        skill_id="x",
        category="redundant_lookup",
        observed_pattern="",
        evidence=evidence,
        estimated_cost_pct=-50.0,
        estimated_latency_pct=-30.0,
        quality_risk="low",
        occurrences=occurrences,
    )


def test_directive_cites_single_file_by_basename() -> None:
    """Absolute paths → basename only, matches SKILL.md voice."""
    long_path = "/Users/x/work/skill_dir/sample_inputs/policy_001/policy.txt"
    f = _finding(
        _pattern_evidence(tool_name="Read", input_dict={"file_path": long_path}),
        occurrences=3,
    )
    proposal = propose_preload_file(f, current_skill_text=_SKILL)
    after = proposal.patch.after_text
    assert "policy.txt" in after
    assert long_path not in after  # absolute path stripped
    assert "3 captured runs" in after


def test_directive_cites_multiple_files_for_multi_pattern_finding() -> None:
    """A finding with N evidence entries (different files) → one directive listing all."""
    f = _finding(
        _pattern_evidence(tool_name="Read", input_dict={"file_path": "/abs/policy.txt"}),
        _pattern_evidence(tool_name="Read", input_dict={"file_path": "/abs/framework.json"}),
        _pattern_evidence(tool_name="Read", input_dict={"file_path": "/abs/checklist.md"}),
        occurrences=3,
    )
    proposal = propose_preload_file(f, current_skill_text=_SKILL)
    after = proposal.patch.after_text
    assert "policy.txt" in after
    assert "framework.json" in after
    assert "checklist.md" in after
    # Three patterns get one combined directive, not three stacked blockquotes.
    assert after.count("Optimizer note (D001 — preload_file)") == 1


def test_directive_dedupes_repeated_basenames() -> None:
    """Two findings with the same basename but different parent dirs → cited once."""
    f = _finding(
        _pattern_evidence(tool_name="Read", input_dict={"file_path": "/a/policy.txt"}),
        _pattern_evidence(tool_name="Read", input_dict={"file_path": "/b/policy.txt"}),
        occurrences=3,
    )
    proposal = propose_preload_file(f, current_skill_text=_SKILL)
    after = proposal.patch.after_text
    # Basename should appear once in the file-list clause (deduped), not twice.
    assert after.count("`policy.txt`") == 1


def test_directive_for_bash_cat_uses_basename() -> None:
    f = _finding(
        _pattern_evidence(
            tool_name="Bash",
            input_dict={"command": "cat /etc/config/app.yaml"},
        ),
        occurrences=3,
    )
    proposal = propose_preload_file(f, current_skill_text=_SKILL)
    after = proposal.patch.after_text
    assert "app.yaml" in after
    assert "/etc/config/app.yaml" not in after  # basename only


def test_directive_falls_back_when_no_file_extractable() -> None:
    f = _finding(
        _pattern_evidence(tool_name="Glob", input_dict={"pattern": "**/*.py"}),
        occurrences=4,
    )
    proposal = propose_preload_file(f, current_skill_text=_SKILL)
    after = proposal.patch.after_text
    assert "Glob" in after
    assert "4 captured runs" in after
    assert "{tool}" not in after


def test_directive_handles_mixed_file_and_non_file_patterns() -> None:
    f = _finding(
        _pattern_evidence(tool_name="Read", input_dict={"file_path": "/a/policy.txt"}),
        _pattern_evidence(tool_name="Glob", input_dict={"pattern": "**/*.py"}),
        occurrences=3,
    )
    proposal = propose_preload_file(f, current_skill_text=_SKILL)
    after = proposal.patch.after_text
    assert "policy.txt" in after
    assert "Glob" in after


def test_directive_falls_back_when_evidence_missing_fields() -> None:
    """Backward-compat: evidence without tool_name/input fields."""
    f = Finding(
        finding_id="f-001",
        detector_id="D001",
        skill_id="x",
        category="redundant_lookup",
        observed_pattern="",
        evidence=({"trace_ref": "run_001.jsonl", "fragment": "x"},),
        estimated_cost_pct=-50.0,
        estimated_latency_pct=-30.0,
        quality_risk="low",
        occurrences=3,
    )
    proposal = propose_preload_file(f, current_skill_text=_SKILL)
    after = proposal.patch.after_text
    assert "Each `Read`" in after  # generic fallback
