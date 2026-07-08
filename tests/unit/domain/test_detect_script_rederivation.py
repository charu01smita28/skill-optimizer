"""Tests for ``detect_script_rederivation`` (D012)."""
from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ToolUseBlock

from skill_optimizer.config.pricing import Pricing, load_pricing
from skill_optimizer.domain.detectors import detect_script_rederivation
from skill_optimizer.domain.trace import Trace
from skill_optimizer.ports.trace_store import CapturedRun

_PRICING_PATH = Path(__file__).resolve().parents[3] / "config" / "pricing.yaml"

_SCRIPT = '''import json


def validate_invoice(invoice):
    return {"valid": True}


with open("in.json") as f:
    print(validate_invoice(json.load(f)))
'''

_SCRIPT_WITH_HELPER = '''import json
from decimal import Decimal, ROUND_HALF_UP


def round_2dp(value):
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def validate_invoice(invoice):
    return {"valid": True, "x": round_2dp(1.234)}
'''

_BASH_HEREDOC = (
    "python3 << 'EOF'\n"
    "import json\n\n"
    "def validate_invoice(invoice):\n"
    "    return {'valid': True}\n"
    "EOF"
)


@pytest.fixture(scope="module")
def pricing() -> Pricing:
    return load_pricing(_PRICING_PATH)


def _write(file_path: str, content: str, block_id: str) -> ToolUseBlock:
    return ToolUseBlock(
        id=block_id, name="Write", input={"file_path": file_path, "content": content}
    )


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
        run_id=run_id, input_filename="x.json", input_text="{}", output={},
        trace=trace, elapsed_s=1.0,
    )


def _write_script_trace(path: str = "validate.py", code: str = _SCRIPT) -> Trace:
    return _trace([_assistant([_read("/in.json", "r1"), _write(path, code, "w1")])])


def test_emits_finding_when_function_written_across_runs(pricing):
    runs = [_run(i, _write_script_trace()) for i in range(3)]
    findings = detect_script_rederivation(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_id == "D012"
    assert f.category == "script_rederivation"
    assert f.occurrences == 3
    assert "validate_invoice" in f.observed_pattern
    assert f.estimated_cost_pct <= 0
    assert f.evidence[0]["origin"] == "write"
    assert "def validate_invoice" in f.evidence[0]["code"]


def test_no_finding_below_threshold(pricing):
    runs = [_run(i, _write_script_trace()) for i in range(2)]  # < default min_occurrences=3
    assert detect_script_rederivation(skill_id="x", runs=runs, pricing=pricing) == []


def test_detects_inline_python_in_bash(pricing):
    runs = [_run(i, _trace([_assistant([_bash(_BASH_HEREDOC, "b1")])])) for i in range(3)]
    findings = detect_script_rederivation(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    assert findings[0].evidence[0]["origin"] == "bash"
    assert "validate_invoice" in findings[0].observed_pattern


def test_prefers_write_origin_in_evidence(pricing):
    """A run that re-derives the function both inline and via Write: the Write (clean
    full source) is the evidence chosen for that run.
    """
    runs = [
        _run(i, _trace([_assistant([
            _bash(_BASH_HEREDOC, "b1"), _write("validate.py", _SCRIPT, "w1"),
        ])]))
        for i in range(3)
    ]
    findings = detect_script_rederivation(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    assert all(e["origin"] == "write" for e in findings[0].evidence)


def test_picks_most_recurring_function_and_lists_subhelpers(pricing):
    """validate_invoice (in 4 runs) is the script; round_2dp (in 3 of those) is a sub-helper."""
    with_helper = [_run(i, _write_script_trace(code=_SCRIPT_WITH_HELPER)) for i in range(3)]
    without = [_run(99, _write_script_trace(code=_SCRIPT))]
    findings = detect_script_rederivation(skill_id="x", runs=with_helper + without, pricing=pricing)
    assert len(findings) == 1
    f = findings[0]
    assert "`validate_invoice(...)`" in f.observed_pattern
    assert "round_2dp" in f.observed_pattern
    assert f.occurrences == 4


def test_ignores_non_py_write(pricing):
    """A Write of a .txt file is not a script artifact even if its content has a `def` line."""
    runs = [
        _run(i, _trace([_assistant([_write("notes.txt", "def foo(): pass\n", "w1")])]))
        for i in range(3)
    ]
    assert detect_script_rederivation(skill_id="x", runs=runs, pricing=pricing) == []


def test_no_finding_when_no_functions_authored(pricing):
    """Reads only — nothing re-derived."""
    runs = [_run(i, _trace([_assistant([_read("/in.json", "r1")])])) for i in range(3)]
    assert detect_script_rederivation(skill_id="x", runs=runs, pricing=pricing) == []


def test_evidence_capped_but_occurrences_counts_all(pricing):
    runs = [_run(i, _write_script_trace()) for i in range(10)]
    findings = detect_script_rederivation(skill_id="x", runs=runs, pricing=pricing)
    assert len(findings) == 1
    assert findings[0].occurrences == 10
    assert len(findings[0].evidence) == 6  # _MAX_EVIDENCE_RUNS


def test_no_finding_on_empty_runs(pricing):
    assert detect_script_rederivation(skill_id="x", runs=[], pricing=pricing) == []
