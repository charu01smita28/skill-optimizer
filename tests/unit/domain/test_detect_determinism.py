"""Tests for ``detect_deterministic_steps`` (D005)."""
from __future__ import annotations

from pathlib import Path

from skill_optimizer.domain.detectors import detect_deterministic_steps
from skill_optimizer.domain.trace import Trace
from skill_optimizer.ports.trace_store import CapturedRun


def _skill_with_primary_fields(tmp_path: Path, fields: list[str]) -> Path:
    """Helper: build a minimal skill dir whose SKILL.md declares the given primary_fields."""
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    fm = '---\nname: x\nprimary_fields: [' + ", ".join(f'"{f}"' for f in fields) + ']\n---\n# x\n'
    (skill_dir / "SKILL.md").write_text(fm)
    return skill_dir


def _trace() -> Trace:
    return Trace(
        session_id="s", cwd="/tmp", initial_prompt="p",
        version="1.0", is_sidechain=False, messages=(),
    )


def _run(run_id: int, input_filename: str, output: dict, input_text: str = "ticket body") -> CapturedRun:
    return CapturedRun(
        run_id=run_id,
        input_filename=input_filename,
        input_text=input_text,
        output=output,
        trace=_trace(),
        elapsed_s=10.0,
    )


def _replays(start_id: int, input_filename: str, outputs: list[dict]) -> list[CapturedRun]:
    return [_run(start_id + i, input_filename, out) for i, out in enumerate(outputs)]


# ---------- fires --------------------------------------------------------------

def test_fires_full_when_all_outputs_byte_identical() -> None:
    out_a = {"team": "billing", "priority": "high", "category": "renewal_risk"}
    out_b = {"team": "support", "priority": "medium", "category": "account_access"}
    runs = (
        _replays(1, "ticket_001.txt", [dict(out_a), dict(out_a), dict(out_a)])
        + _replays(4, "ticket_002.txt", [dict(out_b), dict(out_b), dict(out_b)])
    )
    findings = detect_deterministic_steps("ticket_router", runs)
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_id == "D005"
    assert f.category == "deterministic_steps"
    assert f.occurrences == 2
    assert f.estimated_cost_pct == -100.0
    assert f.evidence[0]["classification"] == "full"
    assert f.evidence[0]["stable_fields_corpuswide"] == ["category", "priority", "team"]


def test_fires_full_primary_when_only_freetext_varies(tmp_path: Path) -> None:
    base = {"team": "billing", "priority": "high", "category": "renewal_risk"}
    runs = (
        _replays(1, "t1.txt", [base | {"rationale": "a"}, base | {"rationale": "b"}, base | {"rationale": "c"}])
        + _replays(4, "t2.txt", [
            {"team": "support", "priority": "medium", "category": "x", "rationale": "p"},
            {"team": "support", "priority": "medium", "category": "x", "rationale": "q"},
        ])
    )
    skill_dir = _skill_with_primary_fields(tmp_path, ["team", "priority", "category"])
    findings = detect_deterministic_steps("ticket_router", runs, skill_dir=skill_dir)
    assert len(findings) == 1
    assert findings[0].evidence[0]["classification"] == "full_primary"
    # every primary field stable; non-primary "rationale" excluded from the universe
    assert findings[0].evidence[0]["stable_fields_corpuswide"] == ["category", "priority", "team"]
    assert "rationale" not in findings[0].evidence[0]["field_universe"]


def test_fires_partial_when_one_primary_field_varies_on_one_input(tmp_path: Path) -> None:
    # Mirrors the real ticket_router corpus: category stable everywhere, priority fuzzy.
    runs = (
        _replays(1, "t1.txt", [
            {"team": "billing", "priority": "high", "category": "renewal_risk"},
            {"team": "billing", "priority": "high", "category": "renewal_risk"},
            {"team": "billing", "priority": "urgent", "category": "renewal_risk"},  # priority differs
        ])
        + _replays(4, "t2.txt", [
            {"team": "support", "priority": "medium", "category": "account_access"},
            {"team": "support", "priority": "medium", "category": "account_access"},
        ])
    )
    skill_dir = _skill_with_primary_fields(tmp_path, ["team", "priority", "category"])
    findings = detect_deterministic_steps("ticket_router", runs, skill_dir=skill_dir)
    assert len(findings) == 1
    ev0 = findings[0].evidence[0]
    assert ev0["classification"] == "partial"
    assert ev0["stable_fields_corpuswide"] == ["category", "team"]  # priority dropped
    assert findings[0].estimated_cost_pct < 0.0  # proportional partial estimate
    assert findings[0].estimated_cost_pct > -100.0


# ---------- abstains -----------------------------------------------------------

def test_no_finding_when_no_field_stable_anywhere() -> None:
    runs = (
        _replays(1, "t1.txt", [
            {"team": "a", "priority": "high", "category": "x"},
            {"team": "b", "priority": "low", "category": "y"},
        ])
        + _replays(3, "t2.txt", [
            {"team": "c", "priority": "high", "category": "z"},
            {"team": "d", "priority": "low", "category": "w"},
        ])
    )
    assert detect_deterministic_steps("ticket_router", runs) == []


def test_no_finding_when_too_few_inputs() -> None:
    out = {"team": "billing", "priority": "high", "category": "x"}
    runs = _replays(1, "only_one.txt", [dict(out), dict(out), dict(out)])
    assert detect_deterministic_steps("ticket_router", runs) == []


def test_no_finding_when_too_few_replays() -> None:
    runs = [
        _run(1, "t1.txt", {"team": "billing", "priority": "high", "category": "x"}),
        _run(2, "t2.txt", {"team": "support", "priority": "low", "category": "y"}),
    ]
    assert detect_deterministic_steps("ticket_router", runs) == []


def test_skips_failed_and_empty_outputs() -> None:
    out = {"team": "billing", "priority": "high", "category": "x"}
    runs = (
        _replays(1, "t1.txt", [dict(out), dict(out), {"_error": "boom"}])  # one failed replay dropped
        + _replays(4, "t2.txt", [dict(out), {}, dict(out)])                 # one empty replay dropped
        + _replays(7, "t3.txt", [{"_parse_error": "x"}, {"_parse_error": "y"}])  # all-bad → input dropped
    )
    findings = detect_deterministic_steps("ticket_router", runs)
    assert len(findings) == 1
    # t3 contributed nothing; t1 and t2 each kept 2 good replays
    assert findings[0].occurrences == 2


def test_empty_runs_returns_empty() -> None:
    assert detect_deterministic_steps("ticket_router", []) == []


# ---------- evidence shape -----------------------------------------------------

def test_evidence_shape() -> None:
    out = {"team": "billing", "priority": "high", "category": "renewal_risk"}
    runs = (
        _replays(1, "t1.txt", [dict(out), dict(out)], )
        + _replays(3, "t2.txt", [
            {"team": "support", "priority": "low", "category": "x"},
            {"team": "support", "priority": "low", "category": "x"},
        ])
    )
    f = detect_deterministic_steps("ticket_router", runs)[0]
    ev = f.evidence[0]
    for key in ("input_filename", "trace_ref", "n_replays", "input_text",
                "stable_fields", "stable_values", "representative_output",
                "full_output_identical", "classification",
                "stable_fields_corpuswide", "field_universe"):
        assert key in ev, f"missing evidence key: {key}"
    assert ev["n_replays"] == 2
    assert ev["full_output_identical"] is True
    assert ev["representative_output"] == out
    # later evidence entries don't carry the corpus-level summary keys
    assert "classification" not in f.evidence[1]


def test_unregistered_skill_uses_common_output_keys() -> None:
    runs = (
        _replays(1, "a.txt", [{"verdict": "yes", "score": 9, "note": "p"},
                              {"verdict": "yes", "score": 9, "note": "q"}])
        + _replays(3, "b.txt", [{"verdict": "no", "score": 2, "extra": 1},
                                {"verdict": "no", "score": 2, "extra": 2}])
    )
    f = detect_deterministic_steps("totally_unregistered_skill", runs)[0]
    # field universe = keys present in EVERY output = {verdict, score}; "note"/"extra" excluded
    assert sorted(f.evidence[0]["field_universe"]) == ["score", "verdict"]
    assert f.evidence[0]["stable_fields_corpuswide"] == ["score", "verdict"]
