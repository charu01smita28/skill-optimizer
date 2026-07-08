"""Tests for ``propose_tool_guidance_rewrite``. Stub ``LLMClient`` covers prompt
assembly, evidence formatting, error paths, and patch construction.
"""
from __future__ import annotations

from dataclasses import dataclass

from skill_optimizer.domain.mutations import propose_tool_guidance_rewrite
from skill_optimizer.domain.types import Finding
from skill_optimizer.ports.llm_client import LLMClientError


_SKILL_TEXT = (
    "---\n"
    "name: x\n"
    "model: claude-haiku-4-5\n"
    "---\n"
    "\n"
    "# Demo Skill\n"
    "\n"
    "## Process\n"
    "\n"
    "1. Run the script.\n"
    "2. Save the output.\n"
)


@dataclass
class _StubLLM:
    """Records the last call; returns a canned response or raises."""
    response: str = ""
    raise_error: bool = False
    last_system: str = ""
    last_user: str = ""
    last_model: str = ""

    def complete(self, system: str, user: str, model: str = "") -> str:
        self.last_system = system
        self.last_user = user
        self.last_model = model
        if self.raise_error:
            raise LLMClientError("stub error")
        return self.response


def _failure_evidence(
    *,
    tool: str = "Bash",
    failed_input: dict | None = None,
    retry_input: dict | None = None,
    error_excerpt: str = "no such file",
) -> dict:
    return {
        "tool": tool,
        "failed_input": failed_input or {"command": "python script.py"},
        "retry_input": retry_input or {"command": "python ./script.py"},
        "error_excerpt": error_excerpt,
        "trace_refs": ["run_001.jsonl"],
        "occurrences": 2,
        "estimated_cost_pct": -8.0,
    }


def _finding(*evidence: dict, occurrences: int = 2) -> Finding:
    return Finding(
        finding_id="f-003",
        detector_id="D003",
        skill_id="x",
        category="tool_execution_misses",
        observed_pattern="",
        evidence=evidence,
        estimated_cost_pct=-8.0,
        estimated_latency_pct=-6.4,
        quality_risk="low",
        occurrences=occurrences,
    )


_VALID_REWRITE = (
    "---\n"
    "name: x\n"
    "model: claude-haiku-4-5\n"
    "---\n"
    "\n"
    "# Demo Skill\n"
    "\n"
    "## Tool Usage Guidance\n"
    "\n"
    "When invoking Bash to run Python scripts, always use a relative path "
    "prefix (`./script.py`), not the bare filename. Bare-filename invocations "
    "fail with 'No such file' on the first attempt.\n"
    "\n"
    "## Process\n"
    "\n"
    "1. Run the script using `python ./script.py`.\n"
    "2. Save the output.\n"
)


def test_rewriter_receives_evidence_with_tool_and_inputs() -> None:
    f = _finding(
        _failure_evidence(
            tool="Bash",
            failed_input={"command": "python script.py"},
            retry_input={"command": "python ./script.py"},
            error_excerpt="No such file: 'script.py'",
        ),
    )
    llm = _StubLLM(response=_VALID_REWRITE)
    proposal = propose_tool_guidance_rewrite(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is not None
    assert "Bash" in llm.last_user
    assert "python script.py" in llm.last_user
    assert "python ./script.py" in llm.last_user
    assert "No such file" in llm.last_user
    assert "2 captured runs" in llm.last_user


def test_proposal_carries_full_file_patch_and_tier_2() -> None:
    f = _finding(_failure_evidence())
    llm = _StubLLM(response=_VALID_REWRITE)
    proposal = propose_tool_guidance_rewrite(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is not None
    assert proposal.tier == "2"
    assert proposal.mutation_type == "tool_guidance_rewrite"
    assert proposal.patch.target_relative_path == "SKILL.md"
    assert proposal.patch.full_file is True
    assert proposal.patch.before_text == _SKILL_TEXT
    assert proposal.patch.after_text == _VALID_REWRITE


def test_preamble_is_stripped_before_validation() -> None:
    contaminated = "Sure, here's the rewrite:\n\n" + _VALID_REWRITE
    llm = _StubLLM(response=contaminated)
    f = _finding(_failure_evidence())

    proposal = propose_tool_guidance_rewrite(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is not None
    assert proposal.patch.after_text.startswith("---")
    assert "Sure, here's" not in proposal.patch.after_text


def test_returns_none_when_llm_raises() -> None:
    llm = _StubLLM(raise_error=True)
    f = _finding(_failure_evidence())

    proposal = propose_tool_guidance_rewrite(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is None


def test_returns_none_when_response_lacks_frontmatter() -> None:
    llm = _StubLLM(response="this is not a SKILL.md, just plain text")
    f = _finding(_failure_evidence())

    proposal = propose_tool_guidance_rewrite(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is None


def test_returns_none_when_evidence_is_empty() -> None:
    """Skip mutation rather than burning an LLM call."""
    f = Finding(
        finding_id="f-003",
        detector_id="D003",
        skill_id="x",
        category="tool_execution_misses",
        observed_pattern="",
        evidence=(),
        estimated_cost_pct=0.0,
        estimated_latency_pct=0.0,
        quality_risk="low",
        occurrences=2,
    )
    llm = _StubLLM(response=_VALID_REWRITE)

    proposal = propose_tool_guidance_rewrite(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is None
    assert llm.last_user == ""


def test_returns_none_when_evidence_missing_required_fields() -> None:
    """Evidence entry without tool/failed_input/retry_input → no LLM call."""
    f = _finding({"trace_ref": "run_001.jsonl"})
    llm = _StubLLM(response=_VALID_REWRITE)

    proposal = propose_tool_guidance_rewrite(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is None
    assert llm.last_user == ""
