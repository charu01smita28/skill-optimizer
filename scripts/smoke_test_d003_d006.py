"""Synthetic-trace smoke test for D003 + D006. Run: python scripts/smoke_test_d003_d006.py"""
from __future__ import annotations

from claude_agent_sdk import (
    AssistantMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from skill_optimizer.config.pricing import load_pricing
from skill_optimizer.domain.detectors import (
    detect_env_setup_repeat,
    detect_tool_reliability_failures,
)
from skill_optimizer.domain.trace import Trace
from skill_optimizer.ports.trace_store import CapturedRun


PRICING = load_pricing()


def _bash(command: str, block_id: str) -> ToolUseBlock:
    return ToolUseBlock(id=block_id, name="Bash", input={"command": command})


def _assistant(blocks: list) -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks),
        model="claude-haiku-4-5",
        usage={"input_tokens": 100, "output_tokens": 50},
        message_id="msg",
        stop_reason="end_turn",
        session_id="sess",
        uuid="uuid",
    )


def _result(tool_use_id: str, content: str, is_error: bool = False) -> UserMessage:
    return UserMessage(
        content=[ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error)],
        uuid="uuid-result",
        tool_use_result=None,
    )


def _trace(messages: list) -> Trace:
    return Trace(
        session_id="sess",
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
        input_text="",
        output={},
        trace=trace,
        elapsed_s=1.0,
    )


def smoke_d003() -> None:
    print("=" * 70)
    print("D003 ToolReliability — failure→similar-retry detection")
    print("=" * 70)

    failure_retry_trace = _trace([
        _assistant([_bash("python script.py", "tu-1")]),
        _result("tu-1", "python: can't open file 'script.py': No such file", is_error=True),
        _assistant([_bash("python ./script.py", "tu-2")]),
        _result("tu-2", "Hello world", is_error=False),
    ])
    runs = [_run(i, failure_retry_trace) for i in range(3)]

    findings = detect_tool_reliability_failures(
        skill_id="synthetic_d003", runs=runs, pricing=PRICING,
    )

    print(f"\nFindings emitted: {len(findings)}")
    for f in findings:
        print(f"\n  Finding ID:        {f.finding_id}")
        print(f"  Detector:          {f.detector_id}")
        print(f"  Category:          {f.category}")
        print(f"  Occurrences:       {f.occurrences}")
        print(f"  Estimated cost:    {f.estimated_cost_pct:.1f}%")
        print(f"  Evidence patterns: {len(f.evidence)}")
        for i, e in enumerate(f.evidence, 1):
            print(f"\n  Pattern {i}:")
            print(f"    Tool:          {e['tool']}")
            print(f"    Failed input:  {e['failed_input']}")
            print(f"    Error excerpt: {e['error_excerpt']}")
            print(f"    Retry input:   {e['retry_input']}")
            print(f"    Cost:          {e['estimated_cost_pct']:.1f}%")

    if not findings:
        print("\n  ✗ NO FINDINGS — detector did not fire on the synthetic pattern")
    else:
        print("\n  ✓ D003 fires correctly on failure→retry pattern")


def smoke_d006() -> None:
    print("\n" + "=" * 70)
    print("D006 EnvSetupRepeat — recurring install/download detection")
    print("=" * 70)

    multi_install_trace = _trace([
        _assistant([_bash("pip install pandas", "tu-1")]),
        _result("tu-1", "Successfully installed pandas-2.0", is_error=False),
        _assistant([_bash("apt-get install -y curl", "tu-2")]),
        _result("tu-2", "curl is already the newest version", is_error=False),
        _assistant([_bash("curl -O https://example.com/data.csv", "tu-3")]),
        _result("tu-3", "100% downloaded", is_error=False),
    ])
    runs = [_run(i, multi_install_trace) for i in range(3)]

    findings = detect_env_setup_repeat(
        skill_id="synthetic_d006", runs=runs, pricing=PRICING,
    )

    print(f"\nFindings emitted: {len(findings)}")
    for f in findings:
        print(f"\n  Finding ID:        {f.finding_id}")
        print(f"  Detector:          {f.detector_id}")
        print(f"  Category:          {f.category}")
        print(f"  Occurrences:       {f.occurrences}")
        print(f"  Estimated cost:    {f.estimated_cost_pct:.1f}%")
        print(f"  Evidence patterns: {len(f.evidence)}")
        for i, e in enumerate(f.evidence, 1):
            print(f"\n  Pattern {i}:")
            print(f"    Family:    {e['family']}")
            print(f"    Target:    {e['target']}")
            print(f"    Command:   {e['command_excerpt']}")
            print(f"    Cost:      {e['estimated_cost_pct']:.1f}%")

    if not findings:
        print("\n  ✗ NO FINDINGS — detector did not fire on the synthetic pattern")
    else:
        print("\n  ✓ D006 fires correctly on recurring install/download patterns")


def smoke_d003_negative() -> None:
    print("\n" + "=" * 70)
    print("D003 negative — no failure → no finding (regression check)")
    print("=" * 70)

    no_error_trace = _trace([
        _assistant([_bash("python ./script.py", "tu-1")]),
        _result("tu-1", "Hello world", is_error=False),
        _assistant([_bash("python ./script.py", "tu-2")]),
        _result("tu-2", "Hello again", is_error=False),
    ])
    runs = [_run(i, no_error_trace) for i in range(3)]

    findings = detect_tool_reliability_failures(
        skill_id="synthetic_d003_neg", runs=runs, pricing=PRICING,
    )
    print(f"\nFindings emitted: {len(findings)} (expected: 0)")
    if not findings:
        print("  ✓ silent on non-error trace — no false positive")
    else:
        print("  ✗ false positive — should not have fired")


def smoke_d006_negative() -> None:
    print("\n" + "=" * 70)
    print("D006 negative — non-install Bash → no finding (regression check)")
    print("=" * 70)

    non_install_trace = _trace([
        _assistant([_bash("ls /tmp", "tu-1")]),
        _result("tu-1", "file1\nfile2", is_error=False),
        _assistant([_bash("pip --version", "tu-2")]),
        _result("tu-2", "pip 23.0", is_error=False),
    ])
    runs = [_run(i, non_install_trace) for i in range(3)]

    findings = detect_env_setup_repeat(
        skill_id="synthetic_d006_neg", runs=runs, pricing=PRICING,
    )
    print(f"\nFindings emitted: {len(findings)} (expected: 0)")
    if not findings:
        print("  ✓ silent on non-install Bash — no false positive")
    else:
        print("  ✗ false positive — should not have fired")


if __name__ == "__main__":
    smoke_d003()
    smoke_d006()
    smoke_d003_negative()
    smoke_d006_negative()
    print("\n" + "=" * 70)
    print("smoke test complete")
    print("=" * 70)
