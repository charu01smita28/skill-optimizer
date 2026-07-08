"""Tests for ``detect_tool_reliability_failures`` (D003)."""
from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from skill_optimizer.config.pricing import Pricing, load_pricing
from skill_optimizer.domain.detectors import detect_tool_reliability_failures
from skill_optimizer.domain.trace import Trace
from skill_optimizer.ports.trace_store import CapturedRun


_PRICING_PATH = Path(__file__).resolve().parents[3] / "config" / "pricing.yaml"


@pytest.fixture(scope="module")
def pricing() -> Pricing:
    return load_pricing(_PRICING_PATH)


# ---------- fixture builders ------------------------------------------------

def _bash(command: str, block_id: str) -> ToolUseBlock:
    return ToolUseBlock(id=block_id, name="Bash", input={"command": command})


def _read(file_path: str, block_id: str) -> ToolUseBlock:
    return ToolUseBlock(id=block_id, name="Read", input={"file_path": file_path})


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


def _user_with_result(tool_use_id: str, content: str, is_error: bool = False) -> UserMessage:
    return UserMessage(
        content=[ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error)],
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


def _bash_failure_retry_trace() -> Trace:
    """Bash('python script.py') errors, then Bash('python ./script.py') succeeds."""
    return _trace([
        _assistant([_bash("python script.py", "tu-1")]),
        _user_with_result("tu-1", "python: can't open file 'script.py': No such file", is_error=True),
        _assistant([_bash("python ./script.py", "tu-2")]),
        _user_with_result("tu-2", "Hello world", is_error=False),
    ])


# ---------- tests -----------------------------------------------------------

def test_emits_finding_for_failure_retry_across_two_traces(pricing):
    runs = [_run(i, _bash_failure_retry_trace()) for i in range(2)]
    findings = detect_tool_reliability_failures(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_id == "D003"
    assert f.category == "tool_execution_misses"
    assert f.occurrences == 2
    assert len(f.evidence) == 1
    e = f.evidence[0]
    assert e["tool"] == "Bash"
    assert e["failed_input"] == {"command": "python script.py"}
    assert e["retry_input"] == {"command": "python ./script.py"}
    assert "No such file" in e["error_excerpt"]


def test_no_finding_when_only_one_trace_shows_pattern(pricing):
    runs = [_run(0, _bash_failure_retry_trace())]
    findings = detect_tool_reliability_failures(skill_id="x", runs=runs, pricing=pricing)
    assert findings == []


def test_no_finding_when_tool_result_not_error(pricing):
    """is_error=False on the first result → no failure-retry pattern."""
    trace = _trace([
        _assistant([_bash("python script.py", "tu-1")]),
        _user_with_result("tu-1", "Hello", is_error=False),
        _assistant([_bash("python ./script.py", "tu-2")]),
        _user_with_result("tu-2", "Hello", is_error=False),
    ])
    runs = [_run(i, trace) for i in range(2)]
    findings = detect_tool_reliability_failures(skill_id="x", runs=runs, pricing=pricing)
    assert findings == []


def test_no_finding_when_no_retry_after_failure(pricing):
    """Failure without retry — no pattern."""
    trace = _trace([
        _assistant([_bash("python script.py", "tu-1")]),
        _user_with_result("tu-1", "no such file", is_error=True),
    ])
    runs = [_run(i, trace) for i in range(3)]
    findings = detect_tool_reliability_failures(skill_id="x", runs=runs, pricing=pricing)
    assert findings == []


def test_no_finding_when_retry_uses_different_tool(pricing):
    """Bash fails, Read fires next — different tool, not a retry pattern."""
    trace = _trace([
        _assistant([_bash("cat /missing.txt", "tu-1")]),
        _user_with_result("tu-1", "no such file", is_error=True),
        _assistant([_read("/existing.txt", "tu-2")]),
        _user_with_result("tu-2", "content", is_error=False),
    ])
    runs = [_run(i, trace) for i in range(3)]
    findings = detect_tool_reliability_failures(skill_id="x", runs=runs, pricing=pricing)
    assert findings == []


def test_no_finding_when_retry_input_too_dissimilar(pricing):
    """Same tool, but inputs are wildly different → not a retry."""
    trace = _trace([
        _assistant([_bash("python script.py", "tu-1")]),
        _user_with_result("tu-1", "no such file", is_error=True),
        _assistant([_bash("ls -la /var/log/system.log /etc/hosts /home", "tu-2")]),
        _user_with_result("tu-2", "ok", is_error=False),
    ])
    runs = [_run(i, trace) for i in range(3)]
    findings = detect_tool_reliability_failures(skill_id="x", runs=runs, pricing=pricing)
    assert findings == []


def test_identical_repeat_after_failure_pairs(pricing):
    """Identical retry counts as a retry — similarity = 1.0 ≥ threshold."""
    trace = _trace([
        _assistant([_bash("python script.py", "tu-1")]),
        _user_with_result("tu-1", "transient error", is_error=True),
        _assistant([_bash("python script.py", "tu-2")]),
        _user_with_result("tu-2", "ok", is_error=False),
    ])
    runs = [_run(i, trace) for i in range(2)]
    findings = detect_tool_reliability_failures(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    e = findings[0].evidence[0]
    assert e["failed_input"] == e["retry_input"]


def test_signature_groups_failures_with_same_keys_across_traces(pricing):
    """Different command strings, same input shape (Bash with command key) → grouped."""
    def trace_a():
        return _trace([
            _assistant([_bash("python a.py", "tu-1")]),
            _user_with_result("tu-1", "a-error", is_error=True),
            _assistant([_bash("python ./a.py", "tu-2")]),
            _user_with_result("tu-2", "ok", is_error=False),
        ])

    def trace_b():
        return _trace([
            _assistant([_bash("python b.py", "tu-1")]),
            _user_with_result("tu-1", "b-error", is_error=True),
            _assistant([_bash("python ./b.py", "tu-2")]),
            _user_with_result("tu-2", "ok", is_error=False),
        ])

    runs = [_run(0, trace_a()), _run(1, trace_b())]
    findings = detect_tool_reliability_failures(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    assert findings[0].occurrences == 2


def test_pairs_with_first_matching_retry_only(pricing):
    """When multiple same-tool retries exist after a failure, only the first pairs."""
    trace = _trace([
        _assistant([_bash("python script.py", "tu-1")]),
        _user_with_result("tu-1", "no such file", is_error=True),
        _assistant([_bash("python ./script.py", "tu-2")]),
        _user_with_result("tu-2", "ok", is_error=False),
        _assistant([_bash("python ./script.py", "tu-3")]),
        _user_with_result("tu-3", "ok", is_error=False),
    ])
    runs = [_run(i, trace) for i in range(2)]
    findings = detect_tool_reliability_failures(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    e = findings[0].evidence[0]
    assert e["retry_input"] == {"command": "python ./script.py"}


def test_no_finding_on_empty_runs_list(pricing):
    findings = detect_tool_reliability_failures(skill_id="x", runs=[], pricing=pricing)
    assert findings == []
