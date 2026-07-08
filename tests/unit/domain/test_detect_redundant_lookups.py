"""Unit tests for D001 RedundantLookupDetector (`detect_redundant_lookups`)."""
from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)

from skill_optimizer.config.pricing import Pricing, load_pricing
from skill_optimizer.domain.detectors import detect_redundant_lookups
from skill_optimizer.domain.trace import Trace
from skill_optimizer.ports.trace_store import CapturedRun


_PRICING_PATH = Path(__file__).resolve().parents[3] / "config" / "pricing.yaml"


@pytest.fixture(scope="module")
def pricing() -> Pricing:
    return load_pricing(_PRICING_PATH)


# ---------- fixture builders ------------------------------------------------

def _read_block(file_path: str, block_id: str) -> ToolUseBlock:
    return ToolUseBlock(
        id=block_id,
        name="Read",
        input={"file_path": file_path},
    )


def _read_block_with_offset(file_path: str, block_id: str, offset: int, limit: int = 100) -> ToolUseBlock:
    return ToolUseBlock(
        id=block_id,
        name="Read",
        input={"file_path": file_path, "offset": offset, "limit": limit},
    )


def _edit_block(file_path: str, block_id: str, old: str, new: str) -> ToolUseBlock:
    return ToolUseBlock(
        id=block_id,
        name="Edit",
        input={"file_path": file_path, "old_string": old, "new_string": new},
    )


def _bash_block(command: str, block_id: str) -> ToolUseBlock:
    return ToolUseBlock(
        id=block_id,
        name="Bash",
        input={"command": command},
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
        input_filename="ticket_001.txt",
        input_text="test",
        output={"team": "support"},
        trace=trace,
        elapsed_s=1.5,
    )


def _redundant_run(run_id: int, *, n_reads: int, file_path: str = "/tmp/x") -> CapturedRun:
    msg = _assistant([
        _read_block(file_path, f"tu-{run_id}-{i}") for i in range(n_reads)
    ])
    return _run(run_id, _trace([msg]))


# ---------- tests -----------------------------------------------------------

def test_emits_finding_when_pattern_recurs_across_three_traces(pricing):
    runs = [_redundant_run(run_id=i, n_reads=3) for i in range(3)]
    findings = detect_redundant_lookups(skill_id="ticket_router", runs=runs, pricing=pricing)
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_id == "D001"
    assert f.skill_id == "ticket_router"
    assert f.category == "redundant_lookup"
    assert f.occurrences == 3
    # Single pattern → one evidence entry (per-pattern, post-consolidation).
    assert len(f.evidence) == 1
    assert f.evidence[0]["occurrences"] == 3


def test_no_finding_when_only_two_traces_show_pattern(pricing):
    """min_occurrences default = 3; two traces is below the cross-trace gate."""
    runs = [_redundant_run(run_id=i, n_reads=3) for i in range(2)]
    findings = detect_redundant_lookups(skill_id="ticket_router", runs=runs, pricing=pricing)
    assert findings == []


def test_no_finding_when_each_trace_only_calls_tool_once(pricing):
    """intra_trace_min default = 2; one Read per trace is below the gate."""
    runs = [_redundant_run(run_id=i, n_reads=1) for i in range(5)]
    findings = detect_redundant_lookups(skill_id="ticket_router", runs=runs, pricing=pricing)
    assert findings == []


def test_evidence_carries_real_tool_name_not_semantic_key_sentinel(pricing):
    """Regression: Layer 1a's semantic key is ('__resource__', 'file::...') for
    Read calls. Evidence must carry the original 'Read' name, not the sentinel."""
    runs = [_redundant_run(run_id=i, n_reads=3, file_path="policy.txt") for i in range(3)]
    findings = detect_redundant_lookups(skill_id="ticket_router", runs=runs, pricing=pricing)
    assert len(findings) == 1
    e = findings[0].evidence[0]
    assert e["tool_name"] == "Read"
    assert "policy.txt" in e["input_summary"]


def test_no_finding_on_empty_runs_list(pricing):
    assert detect_redundant_lookups(skill_id="ticket_router", runs=[], pricing=pricing) == []


def test_different_inputs_tracked_as_distinct_patterns(pricing):
    """Read(/tmp/x) ×2 + Read(/tmp/y) ×2 in each of 3 traces → ONE consolidated
    Finding with two evidence entries (one per pattern). The mutation reads all
    entries to build a single multi-file directive."""
    def _trace_xy() -> Trace:
        return _trace([_assistant([
            _read_block("/tmp/x", "tu-x1"),
            _read_block("/tmp/x", "tu-x2"),
            _read_block("/tmp/y", "tu-y1"),
            _read_block("/tmp/y", "tu-y2"),
        ])])

    runs = [_run(i, _trace_xy()) for i in range(3)]
    findings = detect_redundant_lookups(skill_id="ticket_router", runs=runs, pricing=pricing)
    assert len(findings) == 1
    f = findings[0]
    assert len(f.evidence) == 2
    paths_seen = {e["input"]["file_path"] for e in f.evidence}
    assert paths_seen == {"/tmp/x", "/tmp/y"}


def test_non_tool_use_blocks_are_ignored(pricing):
    """Only ToolUseBlocks contribute. TextBlock + ThinkingBlock + 1 Read → no finding."""
    msg = _assistant([
        TextBlock(text="hello"),
        ThinkingBlock(thinking="...", signature="sig"),
        _read_block("/tmp/x", "tu-1"),
    ])
    runs = [_run(i, _trace([msg])) for i in range(3)]
    findings = detect_redundant_lookups(skill_id="ticket_router", runs=runs, pricing=pricing)
    assert findings == []


# ---------- Layer 1a: Read file_path normalizer --------------------------------

def test_read_with_different_offsets_collapses_to_same_pattern(pricing):
    """Layer 1a: Read('/x') and Read('/x', offset=100) collapse to the same key
    because both fetch the same file. Three traces, each with two Reads of /tmp/x
    where the offsets differ → exactly 1 D001 Finding (occurrences=3)."""
    runs = []
    for i in range(3):
        msg = _assistant([
            _read_block("/tmp/x", f"tu-{i}-1"),
            _read_block_with_offset("/tmp/x", f"tu-{i}-2", offset=100),
        ])
        runs.append(_run(i, _trace([msg])))
    findings = detect_redundant_lookups(skill_id="ticket_router", runs=runs, pricing=pricing)
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_id == "D001"
    assert f.occurrences == 3
    assert "/tmp/x" in f.observed_pattern


def test_non_read_tools_still_strict_match(pricing):
    """Backward compat: non-Read tools (e.g., Edit) keep strict (tool, input)
    matching. Three traces, each with two identical Edits → 1 D001 Finding."""
    runs = []
    for i in range(3):
        msg = _assistant([
            _edit_block("/tmp/x", f"tu-{i}-1", old="foo", new="bar"),
            _edit_block("/tmp/x", f"tu-{i}-2", old="foo", new="bar"),
        ])
        runs.append(_run(i, _trace([msg])))
    findings = detect_redundant_lookups(skill_id="ticket_router", runs=runs, pricing=pricing)
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_id == "D001"
    assert f.occurrences == 3


# ---------- Layer 1b: Bash cat cross-tool normalizer ---------------------------

def test_read_and_bash_cat_collapse_to_same_pattern(pricing):
    """Layer 1b: Read('/x') and Bash('cat /x') fetch the same content via
    different tools. Three traces, each with both calls → exactly 1 D001
    Finding (occurrences=3) thanks to the resource-key collapse."""
    runs = []
    for i in range(3):
        msg = _assistant([
            _read_block("/tmp/x", f"tu-{i}-1"),
            _bash_block("cat /tmp/x", f"tu-{i}-2"),
        ])
        runs.append(_run(i, _trace([msg])))
    findings = detect_redundant_lookups(skill_id="ticket_router", runs=runs, pricing=pricing)
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_id == "D001"
    assert f.occurrences == 3


def test_bash_head_does_not_collapse_with_read(pricing):
    """Safety check: Bash('head /x') is NOT a whole-file fetch — must NOT
    collapse with Read('/x'). Three traces, each with one Read + one head →
    each pattern is count=1 (below intra_trace_min) → no Finding."""
    runs = []
    for i in range(3):
        msg = _assistant([
            _read_block("/tmp/x", f"tu-{i}-1"),
            _bash_block("head /tmp/x", f"tu-{i}-2"),
        ])
        runs.append(_run(i, _trace([msg])))
    findings = detect_redundant_lookups(skill_id="ticket_router", runs=runs, pricing=pricing)
    # If head WERE collapsed with Read (false positive), we'd see 1 Finding
    # with count=2 per trace. Since head correctly stays distinct, both calls
    # are count=1, below intra_trace_min=2 → no Finding.
    assert findings == []
