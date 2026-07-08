"""Tests for ``propose_prompt_rewrite`` (pairs with D007). Stub ``LLMClient``."""
from __future__ import annotations

from dataclasses import dataclass

from skill_optimizer.domain.mutations import propose_prompt_rewrite
from skill_optimizer.domain.types import Finding
from skill_optimizer.ports.llm_client import LLMClientError

_SKILL_TEXT = (
    "---\n"
    "name: demo\n"
    "model: claude-haiku-4-5\n"
    "---\n"
    "\n"
    "# Demo Skill\n"
    "\n"
    "## Background\n"
    "\n"
    "This is a fairly long, ornamental explanation that mostly restates what the\n"
    "rules below already say, with some motivational throat-clearing on top, and a\n"
    "few examples that don't add information beyond the rules they illustrate.\n"
    "\n"
    "## Process\n"
    "\n"
    "1. Read the input file.\n"
    "2. Apply the routing rules.\n"
    "3. Save the JSON result to output.json.\n"
    "\n"
    "## Output schema\n"
    "\n"
    "```json\n"
    "{\"team\": \"string\", \"priority\": \"low | medium | high\"}\n"
    "```\n"
)

_TIGHTENED = (
    "---\n"
    "name: demo\n"
    "model: claude-haiku-4-5\n"
    "---\n"
    "\n"
    "# Demo Skill\n"
    "\n"
    "## Process\n"
    "\n"
    "1. Read the input file.\n"
    "2. Apply the routing rules.\n"
    "3. Save the JSON result to output.json.\n"
    "\n"
    "## Output schema\n"
    "\n"
    "```json\n"
    "{\"team\": \"string\", \"priority\": \"low | medium | high\"}\n"
    "```\n"
)


@dataclass
class _StubLLM:
    response: str = ""
    raise_error: bool = False
    last_system: str = ""
    last_user: str = ""
    last_model: str = ""

    def complete(self, system: str, user: str, model: str = "") -> str:
        self.last_system, self.last_user, self.last_model = system, user, model
        if self.raise_error:
            raise LLMClientError("stub error")
        return self.response


def _d007_finding() -> Finding:
    return Finding(
        finding_id="skopt-2026-05-11-d007-demo-001",
        detector_id="D007",
        skill_id="demo",
        category="verbose_prompt",
        observed_pattern="SKILL.md is biggish; trim it",
        evidence=(
            {"heading": "(preamble)", "chars": 60},
            {"heading": "Background", "chars": 240},
            {"heading": "Process", "chars": 90},
            {"heading": "Output schema", "chars": 70},
            {"skill_md_chars": len(_SKILL_TEXT), "n_runs": 3},
        ),
        estimated_cost_pct=-3.5,
        estimated_latency_pct=-4.2,
        quality_risk="medium",
        occurrences=3,
    )


def test_rewriter_receives_section_sizes() -> None:
    llm = _StubLLM(response=_TIGHTENED)
    proposal = propose_prompt_rewrite(_d007_finding(), current_skill_text=_SKILL_TEXT, llm_client=llm)
    assert proposal is not None
    assert "Background: 240 chars" in llm.last_user
    assert "Process: 90 chars" in llm.last_user


def test_proposal_carries_full_file_patch_tier2_prompt_rewrite() -> None:
    llm = _StubLLM(response=_TIGHTENED)
    proposal = propose_prompt_rewrite(_d007_finding(), current_skill_text=_SKILL_TEXT, llm_client=llm)
    assert proposal is not None
    assert proposal.tier == "2"
    assert proposal.mutation_type == "prompt_rewrite"
    assert proposal.patch.target_relative_path == "SKILL.md"
    assert proposal.patch.full_file is True
    assert proposal.patch.before_text == _SKILL_TEXT
    assert proposal.patch.after_text == _TIGHTENED
    assert "→" in proposal.patch.description


def test_preamble_is_stripped_before_validation() -> None:
    llm = _StubLLM(response="Here's a tighter version:\n\n" + _TIGHTENED)
    proposal = propose_prompt_rewrite(_d007_finding(), current_skill_text=_SKILL_TEXT, llm_client=llm)
    assert proposal is not None
    assert proposal.patch.after_text.startswith("---")
    assert "Here's a tighter" not in proposal.patch.after_text


def test_returns_none_when_llm_raises() -> None:
    llm = _StubLLM(raise_error=True)
    assert propose_prompt_rewrite(_d007_finding(), current_skill_text=_SKILL_TEXT, llm_client=llm) is None


def test_returns_none_when_response_lacks_frontmatter() -> None:
    llm = _StubLLM(response="just some prose, not a SKILL.md")
    assert propose_prompt_rewrite(_d007_finding(), current_skill_text=_SKILL_TEXT, llm_client=llm) is None


def test_returns_none_on_noop_rewrite() -> None:
    llm = _StubLLM(response=_SKILL_TEXT)  # unchanged → no-op
    assert propose_prompt_rewrite(_d007_finding(), current_skill_text=_SKILL_TEXT, llm_client=llm) is None


def test_returns_none_when_not_actually_shorter() -> None:
    bloated = _SKILL_TEXT + "\n## Extra\n\n" + ("padding " * 50) + "\n"
    llm = _StubLLM(response=bloated)  # plausible SKILL.md, but longer than the original
    assert propose_prompt_rewrite(_d007_finding(), current_skill_text=_SKILL_TEXT, llm_client=llm) is None
