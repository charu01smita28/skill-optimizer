"""Tests for ``JsonlTraceStore`` lenient ingestion.

Two layouts:
- captured (manifest.json + run_NNN.jsonl + run_NNN.output.json)
- bring-your-own (just *.jsonl)

Plus the captured-with-missing-output fallback (extract from JSONL trace)."""
from __future__ import annotations

import json
from pathlib import Path

from skill_optimizer.adapters.jsonl_trace_store import JsonlTraceStore


def _user_record(prompt: str) -> dict:
    return {
        "parentUuid": None,
        "type": "user",
        "message": {"role": "user", "content": prompt},
    }


def _assistant_write(file_path: str, content: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": "claude-haiku-4-5",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_001",
                    "name": "Write",
                    "input": {"file_path": file_path, "content": content},
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _make_dirs(tmp_path: Path) -> tuple[Path, Path]:
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    sample_inputs = tmp_path / "sample_inputs"
    sample_inputs.mkdir()
    return baseline, sample_inputs


# ---------- captured layout ----------------------------------------------------

def test_captured_layout_with_parallel_output_files(tmp_path: Path) -> None:
    baseline, sample_inputs = _make_dirs(tmp_path)
    (sample_inputs / "case_001.json").write_text('{"x": 1}')

    _write_jsonl(baseline / "run_001.jsonl", [
        _user_record("Read SKILL.md and the input at sample_inputs/case_001.json, ..."),
        _assistant_write("/some/path/output.json", '{"valid": true}'),
    ])
    (baseline / "run_001.output.json").write_text('{"valid": true, "from": "parallel_file"}')
    (baseline / "manifest.json").write_text(json.dumps({"runs": [
        {"run": 1, "input": "case_001.json", "status": "ok",
         "trace": "run_001.jsonl", "output": "run_001.output.json", "elapsed_s": 12.3},
    ]}))

    runs = JsonlTraceStore(baseline, sample_inputs).list_runs()
    assert len(runs) == 1
    # Parallel file wins over JSONL extraction:
    assert runs[0].output == {"valid": True, "from": "parallel_file"}
    assert runs[0].input_filename == "case_001.json"
    assert runs[0].input_text == '{"x": 1}'
    assert runs[0].elapsed_s == 12.3


def test_captured_layout_extracts_from_jsonl_when_output_field_null(tmp_path: Path) -> None:
    baseline, sample_inputs = _make_dirs(tmp_path)
    (sample_inputs / "case_001.json").write_text("input")

    _write_jsonl(baseline / "run_001.jsonl", [
        _user_record("Read SKILL.md and sample_inputs/case_001.json, ..."),
        _assistant_write("/path/output.json", '{"valid": true, "from": "jsonl"}'),
    ])
    # manifest entry says output is null — historically this run was skipped.
    (baseline / "manifest.json").write_text(json.dumps({"runs": [
        {"run": 1, "input": "case_001.json", "status": "ok",
         "trace": "run_001.jsonl", "output": None, "elapsed_s": 8.0},
    ]}))

    runs = JsonlTraceStore(baseline, sample_inputs).list_runs()
    assert len(runs) == 1
    assert runs[0].output == {"valid": True, "from": "jsonl"}


def test_captured_layout_extracts_when_parallel_output_file_missing(tmp_path: Path) -> None:
    baseline, sample_inputs = _make_dirs(tmp_path)
    (sample_inputs / "case_001.json").write_text("input")

    _write_jsonl(baseline / "run_001.jsonl", [
        _user_record("Read SKILL.md and sample_inputs/case_001.json, ..."),
        _assistant_write("/path/output.json", '{"valid": false}'),
    ])
    # Manifest says output file exists but it doesn't — extract from JSONL instead.
    (baseline / "manifest.json").write_text(json.dumps({"runs": [
        {"run": 1, "input": "case_001.json", "status": "ok",
         "trace": "run_001.jsonl", "output": "run_001.output.json"},
    ]}))

    runs = JsonlTraceStore(baseline, sample_inputs).list_runs()
    assert len(runs) == 1
    assert runs[0].output == {"valid": False}


def test_captured_layout_skips_failed_status(tmp_path: Path) -> None:
    baseline, sample_inputs = _make_dirs(tmp_path)
    _write_jsonl(baseline / "run_001.jsonl", [
        _user_record("Read SKILL.md and sample_inputs/case_001.json, ..."),
        _assistant_write("/path/output.json", '{"valid": true}'),
    ])
    (baseline / "manifest.json").write_text(json.dumps({"runs": [
        {"run": 1, "input": "case_001.json", "status": "failed",
         "trace": "run_001.jsonl", "output": None},
    ]}))

    runs = JsonlTraceStore(baseline, sample_inputs).list_runs()
    assert runs == []


# ---------- bring-your-own layout ---------------------------------------------

def test_byo_layout_synthesizes_from_jsonl_files(tmp_path: Path) -> None:
    baseline, sample_inputs = _make_dirs(tmp_path)
    (sample_inputs / "case_001.json").write_text("input-text-1")
    (sample_inputs / "case_002.json").write_text("input-text-2")

    _write_jsonl(baseline / "session_a.jsonl", [
        _user_record("Read SKILL.md and sample_inputs/case_001.json, save to output.json"),
        _assistant_write("/path/output.json", '{"valid": true, "id": 1}'),
    ])
    _write_jsonl(baseline / "session_b.jsonl", [
        _user_record("Read SKILL.md and sample_inputs/case_002.json, save to output.json"),
        _assistant_write("/path/output.json", '{"valid": false, "id": 2}'),
    ])
    # No manifest, no parallel output files.

    runs = JsonlTraceStore(baseline, sample_inputs).list_runs()
    assert len(runs) == 2
    by_input = {r.input_filename: r for r in runs}
    assert by_input["case_001.json"].output == {"valid": True, "id": 1}
    assert by_input["case_002.json"].output == {"valid": False, "id": 2}
    assert by_input["case_001.json"].input_text == "input-text-1"


def test_byo_layout_skips_traces_without_output_write(tmp_path: Path) -> None:
    baseline, sample_inputs = _make_dirs(tmp_path)
    # JSONL with a Write to a different file — no output.json — should be skipped.
    _write_jsonl(baseline / "session_a.jsonl", [
        _user_record("Read SKILL.md and sample_inputs/case_001.json, ..."),
        _assistant_write("/path/something_else.json", '{"valid": true}'),
    ])
    runs = JsonlTraceStore(baseline, sample_inputs).list_runs()
    assert runs == []


def test_byo_layout_falls_back_to_filename_stem_when_prompt_lacks_input_ref(tmp_path: Path) -> None:
    baseline, sample_inputs = _make_dirs(tmp_path)
    _write_jsonl(baseline / "session_xyz.jsonl", [
        _user_record("Just process this and output the result"),  # no sample_inputs/ ref
        _assistant_write("/path/output.json", '{"valid": true}'),
    ])
    runs = JsonlTraceStore(baseline, sample_inputs).list_runs()
    assert len(runs) == 1
    assert runs[0].input_filename == "session_xyz"  # fallback to trace's stem


def test_byo_layout_returns_empty_when_baseline_dir_missing(tmp_path: Path) -> None:
    baseline = tmp_path / "does_not_exist"
    sample_inputs = tmp_path / "sample_inputs"
    sample_inputs.mkdir()
    runs = JsonlTraceStore(baseline, sample_inputs).list_runs()
    assert runs == []


def test_byo_layout_returns_empty_when_no_jsonls(tmp_path: Path) -> None:
    baseline, sample_inputs = _make_dirs(tmp_path)
    runs = JsonlTraceStore(baseline, sample_inputs).list_runs()
    assert runs == []


# ---------- multiple Writes pick the last one ---------------------------------

def test_extracts_last_write_when_skill_overwrites_output(tmp_path: Path) -> None:
    baseline, sample_inputs = _make_dirs(tmp_path)
    _write_jsonl(baseline / "session_a.jsonl", [
        _user_record("Read SKILL.md and sample_inputs/case_001.json, ..."),
        _assistant_write("/path/output.json", '{"valid": true, "draft": 1}'),
        _assistant_write("/path/output.json", '{"valid": true, "draft": 2}'),
        _assistant_write("/path/output.json", '{"valid": true, "draft": 3}'),
    ])
    runs = JsonlTraceStore(baseline, sample_inputs).list_runs()
    assert len(runs) == 1
    assert runs[0].output == {"valid": True, "draft": 3}
