"""Tests for ``detect_pseudoparallelizable_tool_calls`` (D008)."""
from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from skill_optimizer.config.pricing import Pricing, load_pricing
from skill_optimizer.domain.detectors import detect_pseudoparallelizable_tool_calls
from skill_optimizer.domain.trace import Trace
from skill_optimizer.ports.trace_store import CapturedRun


_PRICING_PATH = Path(__file__).resolve().parents[3] / "config" / "pricing.yaml"


@pytest.fixture(scope="module")
def pricing() -> Pricing:
    return load_pricing(_PRICING_PATH)


# ---------- fixture builders ------------------------------------------------

def _read(file_path: str, block_id: str) -> ToolUseBlock:
    return ToolUseBlock(id=block_id, name="Read", input={"file_path": file_path})


def _glob(pattern: str, block_id: str) -> ToolUseBlock:
    return ToolUseBlock(id=block_id, name="Glob", input={"pattern": pattern})


def _bash(command: str, block_id: str) -> ToolUseBlock:
    return ToolUseBlock(id=block_id, name="Bash", input={"command": command})


def _edit(file_path: str, block_id: str) -> ToolUseBlock:
    return ToolUseBlock(
        id=block_id,
        name="Edit",
        input={"file_path": file_path, "old_string": "x", "new_string": "y"},
    )


def _assistant(blocks: list) -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks),
        model="claude-haiku-4-5",
        usage={"input_tokens": 100, "output_tokens": 50},
        message_id="msg-test",
        stop_reason="end_turn",
        session_id="sess-test",
        uuid="uuid-test",
    )


def _user_with_result(tool_use_id: str, content: str) -> UserMessage:
    return UserMessage(
        content=[ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=False)],
        uuid="uuid-user",
        tool_use_result=None,
    )


def _trace(messages: list) -> Trace:
    return Trace(
        session_id="sess-test",
        cwd="/tmp",
        initial_prompt="test",
        version="1.0",
        is_sidechain=False,
        messages=tuple(messages),
    )


def _run(run_id: int, trace: Trace) -> CapturedRun:
    return CapturedRun(
        run_id=run_id,
        input_filename="x.txt",
        input_text="test",
        output={},
        trace=trace,
        elapsed_s=1.0,
    )


def _independent_three_read_trace() -> Trace:
    return _trace([
        _assistant([_read("/tmp/jira.json", "tu-1")]),
        _user_with_result("tu-1", "{tickets: [...]}"),
        _assistant([_read("/tmp/commits.txt", "tu-2")]),
        _user_with_result("tu-2", "abc123 fix bug"),
        _assistant([_read("/tmp/calendar.txt", "tu-3")]),
        _user_with_result("tu-3", "Mon: standup"),
    ])


# ---------- tests -----------------------------------------------------------

def test_emits_finding_for_three_independent_reads_across_three_traces(pricing):
    runs = [_run(i, _independent_three_read_trace()) for i in range(3)]
    findings = detect_pseudoparallelizable_tool_calls(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_id == "D008"
    assert f.category == "pseudoparallelizable_tool_calls"
    assert f.occurrences == 3
    assert len(f.evidence) == 1
    e = f.evidence[0]
    assert e["tools"] == ["Read", "Read", "Read"]
    assert sorted(Path(p).name for p in e["identifiers"]) == [
        "calendar.txt", "commits.txt", "jira.json",
    ]


def test_no_finding_when_only_two_traces_show_pattern(pricing):
    runs = [_run(i, _independent_three_read_trace()) for i in range(2)]
    findings = detect_pseudoparallelizable_tool_calls(skill_id="x", runs=runs, pricing=pricing)
    assert findings == []


def test_no_finding_for_single_tool_call(pricing):
    trace = _trace([_assistant([_read("/tmp/x", "tu-1")])])
    runs = [_run(i, trace) for i in range(3)]
    findings = detect_pseudoparallelizable_tool_calls(skill_id="x", runs=runs, pricing=pricing)
    assert findings == []


def test_unsafe_tool_breaks_batch(pricing):
    """Bash breaks a candidate run; surviving sub-batches are size-1 → no finding."""
    trace = _trace([
        _assistant([_read("/tmp/a", "tu-1")]),
        _user_with_result("tu-1", "a-content"),
        _assistant([_bash("echo hi", "tu-2")]),
        _user_with_result("tu-2", "hi"),
        _assistant([_read("/tmp/b", "tu-3")]),
        _user_with_result("tu-3", "b-content"),
    ])
    runs = [_run(i, trace) for i in range(3)]
    findings = detect_pseudoparallelizable_tool_calls(skill_id="x", runs=runs, pricing=pricing)
    assert findings == []


def test_dependency_detected_via_substring_match_splits_batch(pricing):
    """Read(index) returns text containing 'b.txt'; next Read('b.txt') depends on it."""
    trace = _trace([
        _assistant([_read("/tmp/index.json", "tu-1")]),
        _user_with_result("tu-1", '{"next_file": "b.txt"}'),
        _assistant([_read("/tmp/b.txt", "tu-2")]),
        _user_with_result("tu-2", "b-content"),
        _assistant([_read("/tmp/c.txt", "tu-3")]),
        _user_with_result("tu-3", "c-content"),
    ])
    runs = [_run(i, trace) for i in range(3)]
    findings = detect_pseudoparallelizable_tool_calls(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    e = findings[0].evidence[0]
    assert sorted(Path(p).name for p in e["identifiers"]) == ["b.txt", "c.txt"]


def test_multi_tool_turn_breaks_batch(pricing):
    """A turn with two ToolUseBlocks isn't single-tool; it already emits in parallel."""
    trace = _trace([
        _assistant([_read("/tmp/a", "tu-1"), _read("/tmp/b", "tu-2")]),
        _user_with_result("tu-1", "a"),
        _user_with_result("tu-2", "b"),
        _assistant([_read("/tmp/c", "tu-3")]),
        _user_with_result("tu-3", "c"),
    ])
    runs = [_run(i, trace) for i in range(3)]
    findings = detect_pseudoparallelizable_tool_calls(skill_id="x", runs=runs, pricing=pricing)
    assert findings == []


def test_glob_calls_with_distinct_patterns_batchable(pricing):
    trace = _trace([
        _assistant([_glob("**/*.py", "tu-1")]),
        _user_with_result("tu-1", "a.py\nb.py"),
        _assistant([_glob("**/*.md", "tu-2")]),
        _user_with_result("tu-2", "x.md\ny.md"),
    ])
    runs = [_run(i, trace) for i in range(3)]
    findings = detect_pseudoparallelizable_tool_calls(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    e = findings[0].evidence[0]
    assert e["tools"] == ["Glob", "Glob"]
    assert e["identifiers"] == ["**/*.py", "**/*.md"]


def test_edit_inside_run_breaks_batch(pricing):
    """Edit mutates state; not in SAFE_TOOLS."""
    trace = _trace([
        _assistant([_read("/tmp/a", "tu-1")]),
        _user_with_result("tu-1", "a"),
        _assistant([_edit("/tmp/c", "tu-2")]),
        _user_with_result("tu-2", "ok"),
        _assistant([_read("/tmp/b", "tu-3")]),
        _user_with_result("tu-3", "b"),
    ])
    runs = [_run(i, trace) for i in range(3)]
    findings = detect_pseudoparallelizable_tool_calls(skill_id="x", runs=runs, pricing=pricing)
    assert findings == []


def test_signature_is_order_independent_across_traces(pricing):
    """Same file set in different orders shares one cross-trace signature."""
    def trace_abc() -> Trace:
        return _trace([
            _assistant([_read("/tmp/a", "1")]),
            _user_with_result("1", "x"),
            _assistant([_read("/tmp/b", "2")]),
            _user_with_result("2", "x"),
            _assistant([_read("/tmp/c", "3")]),
            _user_with_result("3", "x"),
        ])

    def trace_cba() -> Trace:
        return _trace([
            _assistant([_read("/tmp/c", "1")]),
            _user_with_result("1", "x"),
            _assistant([_read("/tmp/b", "2")]),
            _user_with_result("2", "x"),
            _assistant([_read("/tmp/a", "3")]),
            _user_with_result("3", "x"),
        ])

    runs = [_run(0, trace_abc()), _run(1, trace_cba()), _run(2, trace_abc())]
    findings = detect_pseudoparallelizable_tool_calls(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    assert findings[0].occurrences == 3
