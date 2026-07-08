"""Tests for the verifier's three-tier ``primary_fields`` resolver.

Order: SKILL.md frontmatter → auto-derive from baseline replay stability →
all top-level keys of the first baseline output (final fallback)."""
from __future__ import annotations

from pathlib import Path

from skill_optimizer.domain.trace import Trace
from skill_optimizer.domain.verifier import (
    _derive_primary_fields_from_baseline,
    _resolve_primary_fields,
)
from skill_optimizer.ports.trace_store import CapturedRun


def _run(input_name: str, output: dict, run_id: int = 1) -> CapturedRun:
    trace = Trace(
        session_id=f"s{run_id}", cwd="/tmp", initial_prompt="", version="",
        is_sidechain=False, messages=(),
    )
    return CapturedRun(
        run_id=run_id,
        input_filename=input_name,
        input_text="",
        output=output,
        trace=trace,
        elapsed_s=1.0,
    )


def _skill(tmp_path: Path, primary_fields_line: str | None = None) -> Path:
    skill_dir = tmp_path / "stranger_skill"
    skill_dir.mkdir()
    fm = "---\nname: x\n"
    if primary_fields_line:
        fm += f"{primary_fields_line}\n"
    fm += "---\n# x\n"
    (skill_dir / "SKILL.md").write_text(fm)
    return skill_dir


def test_tier1_frontmatter_wins(tmp_path: Path) -> None:
    skill_dir = _skill(tmp_path, 'primary_fields: ["from_frontmatter"]')
    runs = [_run("i1", {"from_frontmatter": "x", "ignored": "noise"}, run_id=1)]
    baseline = {"i1": {"from_frontmatter": "x", "ignored": "noise"}}
    assert _resolve_primary_fields(skill_dir, runs, baseline) == ("from_frontmatter",)


def test_tier2_auto_derive_intersects_stable_keys_across_inputs(tmp_path: Path) -> None:
    skill_dir = _skill(tmp_path, primary_fields_line=None)
    runs = [
        _run("i1", {"a": 1, "b": "stable", "c": "drift-a"}, run_id=1),
        _run("i1", {"a": 1, "b": "stable", "c": "drift-b"}, run_id=2),
        _run("i2", {"a": 2, "b": "stable", "c": "drift-c"}, run_id=3),
        _run("i2", {"a": 2, "b": "stable", "c": "drift-d"}, run_id=4),
    ]
    baseline = {"i1": runs[0].output, "i2": runs[2].output}
    # `a` is stable per input (1 for i1, 2 for i2); `b` is also stable per input;
    # `c` drifts across replays → excluded. Intersection: {a, b}.
    resolved = _resolve_primary_fields(skill_dir, runs, baseline)
    assert resolved == ("a", "b")


def test_tier3_all_keys_when_only_one_replay_per_input(tmp_path: Path) -> None:
    skill_dir = _skill(tmp_path, primary_fields_line=None)
    runs = [_run("i1", {"k1": "v", "k2": 7}, run_id=1)]  # n=1 → can't assess stability
    baseline = {"i1": runs[0].output}
    resolved = _resolve_primary_fields(skill_dir, runs, baseline)
    assert set(resolved) == {"k1", "k2"}


def test_derive_returns_none_when_under_two_replays_per_input() -> None:
    runs = [_run("i1", {"k": "v"}, run_id=1), _run("i2", {"k": "v"}, run_id=2)]
    assert _derive_primary_fields_from_baseline(runs) is None


def test_derive_intersects_across_inputs() -> None:
    runs = [
        _run("i1", {"a": 1, "b": 1}, run_id=1),
        _run("i1", {"a": 1, "b": 1}, run_id=2),   # both stable for i1
        _run("i2", {"a": 1, "b": 2}, run_id=3),
        _run("i2", {"a": 1, "b": 3}, run_id=4),   # only `a` stable for i2
    ]
    assert _derive_primary_fields_from_baseline(runs) == ("a",)


def test_derive_handles_nested_dict_values() -> None:
    nested = {"computed": {"x": 1.0, "y": 2.0}, "valid": True}
    runs = [
        _run("i1", nested, run_id=1),
        _run("i1", dict(nested), run_id=2),       # same nested dict — should be stable
    ]
    assert _derive_primary_fields_from_baseline(runs) == ("computed", "valid")
