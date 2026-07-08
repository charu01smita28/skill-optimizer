"""Tests for ``propose_pseudoparallelize_tools``. Stub ``LLMClient`` covers prompt
assembly, evidence formatting, error paths, and patch construction.
"""
from __future__ import annotations

from dataclasses import dataclass

from skill_optimizer.domain.mutations import propose_pseudoparallelize_tools
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
    "1. Read file_a.txt\n"
    "2. Read file_b.txt\n"
    "3. Read file_c.txt\n"
    "4. Synthesize.\n"
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


def _batch_evidence(*, tools: list[str], identifiers: list[str]) -> dict:
    return {
        "tools": tools,
        "identifiers": identifiers,
        "inputs": [{"file_path": ident} for ident in identifiers],
        "trace_refs": ["run_001.jsonl"],
        "occurrences": 3,
        "estimated_cost_pct": -20.0,
        "estimated_latency_pct": -33.0,
    }


def _finding(*evidence: dict, occurrences: int = 3) -> Finding:
    return Finding(
        finding_id="f-008",
        detector_id="D008",
        skill_id="x",
        category="pseudoparallelizable_tool_calls",
        observed_pattern="",
        evidence=evidence,
        estimated_cost_pct=-20.0,
        estimated_latency_pct=-33.0,
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
    "## Process\n"
    "\n"
    "In your first assistant turn, dispatch three Task subagents in parallel:\n"
    "- Task: read file_a.txt and return its content\n"
    "- Task: read file_b.txt and return its content\n"
    "- Task: read file_c.txt and return its content\n"
    "\n"
    "Then synthesize.\n"
)


def test_rewriter_receives_evidence_with_filenames() -> None:
    f = _finding(
        _batch_evidence(
            tools=["Read", "Read", "Read"],
            identifiers=["/abs/jira.json", "/abs/commits.txt", "/abs/calendar.txt"],
        ),
        occurrences=5,
    )
    llm = _StubLLM(response=_VALID_REWRITE)
    proposal = propose_pseudoparallelize_tools(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is not None
    assert "jira.json" in llm.last_user
    assert "commits.txt" in llm.last_user
    assert "calendar.txt" in llm.last_user
    assert "5 captured runs" in llm.last_user
    assert "Task" in llm.last_system
    assert "subagent" in llm.last_system.lower()


def test_proposal_carries_full_file_patch_and_tier_2() -> None:
    f = _finding(_batch_evidence(tools=["Read", "Read"], identifiers=["/x/a.txt", "/x/b.txt"]))
    llm = _StubLLM(response=_VALID_REWRITE)
    proposal = propose_pseudoparallelize_tools(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is not None
    assert proposal.tier == "2"
    assert proposal.mutation_type == "pseudoparallelize_tools"
    assert proposal.patch.target_relative_path == "SKILL.md"
    assert proposal.patch.full_file is True  # Tier-2 produces a full-file patch
    assert proposal.patch.before_text == _SKILL_TEXT
    assert proposal.patch.after_text == _VALID_REWRITE


def test_preamble_is_stripped_before_validation() -> None:
    contaminated = "I apologize, here is the rewrite:\n\n" + _VALID_REWRITE
    llm = _StubLLM(response=contaminated)
    f = _finding(_batch_evidence(tools=["Read", "Read"], identifiers=["/x/a.txt", "/x/b.txt"]))

    proposal = propose_pseudoparallelize_tools(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is not None
    assert proposal.patch.after_text.startswith("---")
    assert "I apologize" not in proposal.patch.after_text


def test_returns_none_when_llm_raises() -> None:
    llm = _StubLLM(raise_error=True)
    f = _finding(_batch_evidence(tools=["Read", "Read"], identifiers=["/x/a.txt", "/x/b.txt"]))

    proposal = propose_pseudoparallelize_tools(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is None


def test_returns_none_when_response_lacks_frontmatter() -> None:
    llm = _StubLLM(response="not a real SKILL.md just random text")
    f = _finding(_batch_evidence(tools=["Read", "Read"], identifiers=["/x/a.txt", "/x/b.txt"]))

    proposal = propose_pseudoparallelize_tools(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is None


def test_returns_none_when_evidence_is_empty() -> None:
    """Skip mutation rather than burning an LLM call."""
    f = Finding(
        finding_id="f-008",
        detector_id="D008",
        skill_id="x",
        category="pseudoparallelizable_tool_calls",
        observed_pattern="",
        evidence=({"trace_ref": "run_001.jsonl"},),  # malformed: no tools/identifiers
        estimated_cost_pct=-20.0,
        estimated_latency_pct=-33.0,
        quality_risk="low",
        occurrences=3,
    )
    llm = _StubLLM(response=_VALID_REWRITE)

    proposal = propose_pseudoparallelize_tools(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is None
    assert llm.last_user == ""  # LLM must not be called on unusable evidence


def test_returns_none_when_singleton_batch() -> None:
    """Size <2 isn't parallelizable; no LLM call."""
    f = _finding(_batch_evidence(tools=["Read"], identifiers=["/x/a.txt"]))
    llm = _StubLLM(response=_VALID_REWRITE)

    proposal = propose_pseudoparallelize_tools(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is None
    assert llm.last_user == ""


def test_handles_multiple_batches_in_single_finding() -> None:
    """All batches surface in the rewriter evidence body."""
    f = _finding(
        _batch_evidence(tools=["Read", "Read"], identifiers=["/x/a.txt", "/x/b.txt"]),
        _batch_evidence(tools=["Glob", "Glob"], identifiers=["**/*.py", "**/*.md"]),
    )
    llm = _StubLLM(response=_VALID_REWRITE)

    proposal = propose_pseudoparallelize_tools(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is not None
    assert "a.txt" in llm.last_user
    assert "b.txt" in llm.last_user
    assert "Glob" in llm.last_user
