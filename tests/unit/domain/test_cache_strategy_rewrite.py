"""Tests for ``propose_cache_strategy_rewrite``. Stub ``LLMClient`` covers prompt
assembly, evidence formatting, error paths, and patch construction.
"""
from __future__ import annotations

from dataclasses import dataclass

from skill_optimizer.domain.mutations import propose_cache_strategy_rewrite
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
    "1. Install dependencies.\n"
    "2. Run the analysis.\n"
)


@dataclass
class _StubLLM:
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


def _setup_evidence(
    *,
    family: str = "pip_install",
    target: str = "pandas",
    command_excerpt: str = "pip install pandas",
) -> dict:
    return {
        "family": family,
        "target": target,
        "command_excerpt": command_excerpt,
        "trace_refs": ["run_001.jsonl"],
        "occurrences": 2,
        "estimated_cost_pct": -10.0,
    }


def _finding(*evidence: dict, occurrences: int = 2) -> Finding:
    return Finding(
        finding_id="f-006",
        detector_id="D006",
        skill_id="x",
        category="env_setup_repeat",
        observed_pattern="",
        evidence=evidence,
        estimated_cost_pct=-10.0,
        estimated_latency_pct=-8.0,
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
    "## Environment\n"
    "\n"
    "Assume `pandas` is pre-installed in the runtime image. Do not install it.\n"
    "\n"
    "## Process\n"
    "\n"
    "1. Run the analysis.\n"
)


def test_rewriter_receives_evidence_with_family_and_target() -> None:
    f = _finding(_setup_evidence(
        family="pip_install",
        target="pandas",
        command_excerpt="pip install pandas",
    ))
    llm = _StubLLM(response=_VALID_REWRITE)
    proposal = propose_cache_strategy_rewrite(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is not None
    assert "pip_install" in llm.last_user
    assert "pandas" in llm.last_user
    assert "pip install pandas" in llm.last_user
    assert "2 captured runs" in llm.last_user


def test_proposal_carries_full_file_patch_and_tier_2() -> None:
    f = _finding(_setup_evidence())
    llm = _StubLLM(response=_VALID_REWRITE)
    proposal = propose_cache_strategy_rewrite(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is not None
    assert proposal.tier == "2"
    assert proposal.mutation_type == "cache_strategy_rewrite"
    assert proposal.patch.target_relative_path == "SKILL.md"
    assert proposal.patch.full_file is True
    assert proposal.patch.before_text == _SKILL_TEXT
    assert proposal.patch.after_text == _VALID_REWRITE


def test_preamble_is_stripped_before_validation() -> None:
    contaminated = "Here you go:\n\n" + _VALID_REWRITE
    llm = _StubLLM(response=contaminated)
    f = _finding(_setup_evidence())

    proposal = propose_cache_strategy_rewrite(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is not None
    assert proposal.patch.after_text.startswith("---")
    assert "Here you go" not in proposal.patch.after_text


def test_returns_none_when_llm_raises() -> None:
    llm = _StubLLM(raise_error=True)
    f = _finding(_setup_evidence())

    proposal = propose_cache_strategy_rewrite(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is None


def test_returns_none_when_response_lacks_frontmatter() -> None:
    llm = _StubLLM(response="just plain text without a SKILL.md shape")
    f = _finding(_setup_evidence())

    proposal = propose_cache_strategy_rewrite(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is None


def test_returns_none_when_evidence_is_empty() -> None:
    """Skip mutation rather than burning an LLM call."""
    f = Finding(
        finding_id="f-006",
        detector_id="D006",
        skill_id="x",
        category="env_setup_repeat",
        observed_pattern="",
        evidence=(),
        estimated_cost_pct=0.0,
        estimated_latency_pct=0.0,
        quality_risk="low",
        occurrences=2,
    )
    llm = _StubLLM(response=_VALID_REWRITE)

    proposal = propose_cache_strategy_rewrite(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is None
    assert llm.last_user == ""


def test_returns_none_when_evidence_missing_required_fields() -> None:
    """Evidence entry without family/target/command_excerpt → no LLM call."""
    f = _finding({"trace_ref": "run_001.jsonl"})
    llm = _StubLLM(response=_VALID_REWRITE)

    proposal = propose_cache_strategy_rewrite(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is None
    assert llm.last_user == ""


def test_handles_multiple_packages_in_single_finding() -> None:
    """All evidence patterns surface in the rewriter EVIDENCE body."""
    f = _finding(
        _setup_evidence(family="pip_install", target="pandas", command_excerpt="pip install pandas"),
        _setup_evidence(family="pip_install", target="numpy", command_excerpt="pip install numpy"),
    )
    llm = _StubLLM(response=_VALID_REWRITE)

    proposal = propose_cache_strategy_rewrite(f, current_skill_text=_SKILL_TEXT, llm_client=llm)

    assert proposal is not None
    assert "pandas" in llm.last_user
    assert "numpy" in llm.last_user
