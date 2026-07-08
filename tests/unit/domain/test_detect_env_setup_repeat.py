"""Tests for ``detect_env_setup_repeat`` (D006)."""
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
from skill_optimizer.domain.detectors import detect_env_setup_repeat
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


def _pip_install_trace(package: str = "pandas") -> Trace:
    return _trace([
        _assistant([_bash(f"pip install {package}", "tu-1")]),
        _user_with_result("tu-1", "Successfully installed"),
        _assistant([_read("/tmp/data.csv", "tu-2")]),
        _user_with_result("tu-2", "row1,row2"),
    ])


# ---------- tests -----------------------------------------------------------

def test_emits_finding_for_pip_install_across_two_traces(pricing):
    runs = [_run(i, _pip_install_trace("pandas")) for i in range(2)]
    findings = detect_env_setup_repeat(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_id == "D006"
    assert f.category == "env_setup_repeat"
    assert f.occurrences == 2
    e = f.evidence[0]
    assert e["family"] == "pip_install"
    assert e["target"] == "pandas"


def test_no_finding_when_only_one_trace_shows_pattern(pricing):
    runs = [_run(0, _pip_install_trace())]
    findings = detect_env_setup_repeat(skill_id="x", runs=runs, pricing=pricing)
    assert findings == []


def test_normalizes_pip_versioned_target(pricing):
    """`pip install pandas==2.0` normalizes to `pandas` so cross-version installs group."""
    def trace_versioned():
        return _trace([
            _assistant([_bash("pip install pandas==2.0", "tu-1")]),
            _user_with_result("tu-1", "ok"),
        ])

    def trace_bare():
        return _trace([
            _assistant([_bash("pip install pandas", "tu-1")]),
            _user_with_result("tu-1", "ok"),
        ])

    runs = [_run(0, trace_versioned()), _run(1, trace_bare())]
    findings = detect_env_setup_repeat(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    assert findings[0].evidence[0]["target"] == "pandas"


def test_apt_install_pattern(pricing):
    trace = _trace([
        _assistant([_bash("apt-get install -y curl", "tu-1")]),
        _user_with_result("tu-1", "ok"),
    ])
    runs = [_run(i, trace) for i in range(2)]
    findings = detect_env_setup_repeat(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    e = findings[0].evidence[0]
    assert e["family"] == "apt_install"
    assert e["target"] == "curl"


def test_curl_download_pattern_strips_query(pricing):
    """Different query strings collapse to same canonical URL."""
    def trace_q1():
        return _trace([
            _assistant([_bash("curl -O https://example.com/data.csv?token=abc", "tu-1")]),
            _user_with_result("tu-1", "ok"),
        ])

    def trace_q2():
        return _trace([
            _assistant([_bash("curl -O https://example.com/data.csv?token=xyz", "tu-1")]),
            _user_with_result("tu-1", "ok"),
        ])

    runs = [_run(0, trace_q1()), _run(1, trace_q2())]
    findings = detect_env_setup_repeat(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    e = findings[0].evidence[0]
    assert e["family"] == "curl_download"
    assert e["target"] == "https://example.com/data.csv"


def test_no_finding_when_bash_command_is_not_install(pricing):
    """`pip --version` shouldn't match — only the `install` verb does."""
    trace = _trace([
        _assistant([_bash("pip --version", "tu-1")]),
        _user_with_result("tu-1", "pip 23.0"),
    ])
    runs = [_run(i, trace) for i in range(3)]
    findings = detect_env_setup_repeat(skill_id="x", runs=runs, pricing=pricing)
    assert findings == []


def test_no_finding_when_tool_is_not_bash(pricing):
    """Read calls are not env-setup operations."""
    trace = _trace([
        _assistant([_read("/tmp/x", "tu-1")]),
        _user_with_result("tu-1", "ok"),
    ])
    runs = [_run(i, trace) for i in range(3)]
    findings = detect_env_setup_repeat(skill_id="x", runs=runs, pricing=pricing)
    assert findings == []


def test_different_targets_do_not_aggregate(pricing):
    """`pip install pandas` and `pip install numpy` are distinct signatures."""
    def trace_pandas():
        return _trace([
            _assistant([_bash("pip install pandas", "tu-1")]),
            _user_with_result("tu-1", "ok"),
        ])

    def trace_numpy():
        return _trace([
            _assistant([_bash("pip install numpy", "tu-1")]),
            _user_with_result("tu-1", "ok"),
        ])

    runs = [_run(0, trace_pandas()), _run(1, trace_numpy())]
    findings = detect_env_setup_repeat(skill_id="x", runs=runs, pricing=pricing)
    # Each appears in only 1 trace → neither meets min_occurrences=2
    assert findings == []


def test_multiple_packages_aggregated_into_single_finding(pricing):
    """Distinct package patterns each meeting threshold roll up into one Finding with multiple evidence entries."""
    def trace_pandas():
        return _trace([
            _assistant([_bash("pip install pandas", "tu-1")]),
            _user_with_result("tu-1", "ok"),
            _assistant([_bash("pip install numpy", "tu-2")]),
            _user_with_result("tu-2", "ok"),
        ])

    runs = [_run(i, trace_pandas()) for i in range(2)]
    findings = detect_env_setup_repeat(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    targets = sorted(e["target"] for e in findings[0].evidence)
    assert targets == ["numpy", "pandas"]


def test_no_finding_on_empty_runs_list(pricing):
    findings = detect_env_setup_repeat(skill_id="x", runs=[], pricing=pricing)
    assert findings == []


def test_npm_install_pattern(pricing):
    trace = _trace([
        _assistant([_bash("npm install lodash", "tu-1")]),
        _user_with_result("tu-1", "added 1 package"),
    ])
    runs = [_run(i, trace) for i in range(2)]
    findings = detect_env_setup_repeat(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    assert findings[0].evidence[0]["family"] == "npm_install"
    assert findings[0].evidence[0]["target"] == "lodash"
