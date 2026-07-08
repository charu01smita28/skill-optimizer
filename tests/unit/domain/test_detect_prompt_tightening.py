"""Tests for ``detect_verbose_prompt`` (D007)."""
from __future__ import annotations

from claude_agent_sdk import AssistantMessage

from skill_optimizer.domain.detectors import detect_verbose_prompt
from skill_optimizer.domain.trace import Trace
from skill_optimizer.ports.trace_store import CapturedRun


def _trace(input_tokens: int = 1000) -> Trace:
    messages: tuple = ()
    if input_tokens > 0:
        messages = (AssistantMessage(
            content=[],
            model="claude-haiku-4-5",
            usage={"input_tokens": input_tokens, "output_tokens": 200},
            message_id="m", stop_reason="end_turn", session_id="s", uuid="u",
        ),)
    return Trace(
        session_id="s", cwd="/tmp", initial_prompt="p",
        version="1.0", is_sidechain=False, messages=messages,
    )


def _run(run_id: int = 1, input_tokens: int = 1000) -> CapturedRun:
    return CapturedRun(
        run_id=run_id, input_filename="t.txt", input_text="ticket",
        output={"x": 1}, trace=_trace(input_tokens), elapsed_s=10.0,
    )


def _big_skill_md() -> str:
    body_a = ("This is a long ornamental explanation. " * 30).strip()
    body_b = ("Routing rule example that repeats the schema. " * 20).strip()
    return (
        "---\nname: demo\nmodel: claude-haiku-4-5\n---\n\n"
        "# Demo Skill\n\nIntro paragraph.\n\n"
        f"## Background\n\n{body_a}\n\n"
        f"## Process\n\n{body_b}\n\n"
        "## Output schema\n\n```json\n{\"x\": \"number\"}\n```\n"
    )


def _small_skill_md() -> str:
    return (
        "---\nname: demo\nmodel: claude-haiku-4-5\n---\n\n"
        "# Demo Skill\n\n## Process\n\n1. Do the thing.\n2. Save output.json.\n"
    )


def test_fires_when_skill_md_over_threshold() -> None:
    skill_md = _big_skill_md()
    assert len(skill_md) >= 1800
    findings = detect_verbose_prompt("demo", skill_md, [_run(1), _run(2)])
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_id == "D007"
    assert f.category == "verbose_prompt"
    assert f.estimated_cost_pct < 0.0
    assert f.estimated_cost_pct >= -25.0  # frac is capped at 1.0, trim fraction 0.25
    assert f.occurrences == 2
    # evidence carries per-section sizes + a summary row
    headings = {e["heading"] for e in f.evidence if "heading" in e}
    assert "(preamble)" in headings and "Background" in headings and "Process" in headings
    summary = next(e for e in f.evidence if "skill_md_chars" in e)
    assert summary["skill_md_chars"] == len(skill_md)
    assert summary["n_runs"] == 2


def test_no_finding_when_skill_md_small() -> None:
    skill_md = _small_skill_md()
    assert len(skill_md) < 1800
    assert detect_verbose_prompt("demo", skill_md, [_run()]) == []


def test_no_finding_when_no_runs() -> None:
    assert detect_verbose_prompt("demo", _big_skill_md(), []) == []


def test_fallback_cost_when_no_input_tokens() -> None:
    f = detect_verbose_prompt("demo", _big_skill_md(), [_run(1, input_tokens=0)])[0]
    assert f.estimated_cost_pct == -2.0


def test_observed_pattern_names_the_largest_section() -> None:
    f = detect_verbose_prompt("demo", _big_skill_md(), [_run()])[0]
    # "Background" is the padded-largest section
    assert "Background" in f.observed_pattern
