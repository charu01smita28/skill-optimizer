"""Tests for the trace parser.

Uses one of the captured baseline traces as the fixture. These will exist after
running ``python scripts/capture_traces.py`` and are committed into
``traces/ticket_router/baseline/``. The test skips itself if the fixture is
not present so unit tests still pass on a fresh clone before traces are captured.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ToolUseBlock

from skill_optimizer.domain.trace import (
    Trace,
    extract_input_filename,
    extract_output_from_trace,
    parse_trace,
    parse_trace_file,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_DIR = REPO_ROOT / "traces" / "ticket_router" / "baseline"


def _fixture_path() -> Path | None:
    candidate = BASELINE_DIR / "run_001.jsonl"
    return candidate if candidate.exists() else None


@pytest.fixture
def real_trace() -> Trace:
    path = _fixture_path()
    if path is None:
        pytest.skip("baseline trace fixture not yet captured")
    return parse_trace_file(path)


def test_parses_session_id_cwd_version(real_trace: Trace) -> None:
    assert real_trace.session_id, "session_id should be non-empty"
    assert real_trace.cwd, "cwd should be non-empty"
    assert real_trace.version, "version should be non-empty"


def test_initial_prompt_extracted(real_trace: Trace) -> None:
    assert "SKILL.md" in real_trace.initial_prompt
    assert "ticket_001" in real_trace.initial_prompt


def test_has_at_least_one_assistant_message(real_trace: Trace) -> None:
    assert len(real_trace.assistant_messages) >= 1


def test_assistant_messages_carry_token_usage(real_trace: Trace) -> None:
    for m in real_trace.assistant_messages:
        assert isinstance(m, AssistantMessage)
        usage = m.usage or {}
        assert usage.get("output_tokens", 0) >= 0
    assert real_trace.total_input_tokens >= 0


def test_models_used_is_non_empty(real_trace: Trace) -> None:
    assert len(real_trace.models_used) >= 1
    assert all(m.startswith("claude-") for m in real_trace.models_used)


def test_at_least_one_write_tool_use(real_trace: Trace) -> None:
    """The skill saves output.json via Write — every successful run should have one."""
    write_calls = [
        block
        for m in real_trace.assistant_messages
        if isinstance(m.content, list)
        for block in m.content
        if isinstance(block, ToolUseBlock) and block.name == "Write"
    ]
    assert len(write_calls) >= 1, "expected at least one Write tool_use for output.json"


def test_parse_trace_skips_blank_lines() -> None:
    raw = "\n\n\n"
    with pytest.raises(ValueError, match="no valid JSON records"):
        parse_trace(raw)


def test_parse_trace_skips_invalid_lines() -> None:
    """A partial trailing line should not abort the whole parse."""
    valid = (
        '{"type":"user","sessionId":"s1","cwd":"/x","version":"2.1.126",'
        '"parentUuid":null,"message":{"role":"user","content":"hello"}}'
    )
    raw = valid + "\nnot-json-{"
    trace = parse_trace(raw)
    assert trace.session_id == "s1"
    assert trace.initial_prompt == "hello"


# ---------- extract_output_from_trace -----------------------------------------

def _synth_trace(records: list[str]) -> Trace:
    return parse_trace("\n".join(records))


def _user_line(prompt: str) -> str:
    import json as _json
    return _json.dumps({
        "type": "user", "sessionId": "s1", "cwd": "/x", "version": "2.1.126",
        "parentUuid": None,
        "message": {"role": "user", "content": prompt},
    })


def _write_line(file_path: str, content: str) -> str:
    import json as _json
    return _json.dumps({
        "type": "assistant", "sessionId": "s1",
        "message": {
            "role": "assistant", "model": "claude-haiku-4-5",
            "content": [{
                "type": "tool_use", "id": "t1", "name": "Write",
                "input": {"file_path": file_path, "content": content},
            }],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    })


def test_extract_output_finds_write_to_output_json() -> None:
    trace = _synth_trace([
        _user_line("hello"),
        _write_line("/tmp/skill/output.json", '{"valid": true, "score": 9}'),
    ])
    assert extract_output_from_trace(trace) == {"valid": True, "score": 9}


def test_extract_output_returns_none_when_no_matching_write() -> None:
    trace = _synth_trace([
        _user_line("hello"),
        _write_line("/tmp/something_else.json", '{"valid": true}'),
    ])
    assert extract_output_from_trace(trace) is None


def test_extract_output_returns_none_when_content_invalid_json() -> None:
    trace = _synth_trace([
        _user_line("hello"),
        _write_line("/tmp/output.json", "not-json-at-all"),
    ])
    assert extract_output_from_trace(trace) is None


def test_extract_output_picks_last_write_when_multiple() -> None:
    trace = _synth_trace([
        _user_line("hello"),
        _write_line("/tmp/output.json", '{"draft": 1}'),
        _write_line("/tmp/output.json", '{"draft": 2}'),
        _write_line("/tmp/output.json", '{"draft": 3}'),
    ])
    assert extract_output_from_trace(trace) == {"draft": 3}


# ---------- extract_input_filename --------------------------------------------

def test_extract_input_filename_finds_sample_inputs_path() -> None:
    trace = _synth_trace([
        _user_line("Read SKILL.md and the input at /a/sample_inputs/case_001.json, then ..."),
    ])
    assert extract_input_filename(trace, fallback="x") == "case_001.json"


def test_extract_input_filename_uses_fallback_when_no_match() -> None:
    trace = _synth_trace([_user_line("just a generic prompt")])
    assert extract_input_filename(trace, fallback="trace_xyz") == "trace_xyz"


def test_extract_input_filename_uses_fallback_when_initial_prompt_empty() -> None:
    # Synthesize a trace with no user record → initial_prompt is "".
    trace = _synth_trace([_write_line("/tmp/output.json", '{"valid": true}')])
    assert trace.initial_prompt == ""
    assert extract_input_filename(trace, fallback="fb") == "fb"
