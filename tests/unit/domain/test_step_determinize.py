"""Tests for ``propose_step_determinize`` (pairs with D005). Stub ``LLMClient``
covers evidence formatting, error paths, no-op guard, and patch construction.
"""
from __future__ import annotations

from dataclasses import dataclass

from skill_optimizer.domain.mutations import propose_step_determinize
from skill_optimizer.domain.types import Finding
from skill_optimizer.ports.llm_client import LLMClientError

_SKILL_TEXT = (
    "---\n"
    "name: ticket-router\n"
    "model: claude-haiku-4-5\n"
    "---\n"
    "\n"
    "# Ticket Router\n"
    "\n"
    "## Process\n"
    "\n"
    "1. Read the ticket.\n"
    "2. Classify it.\n"
    "3. Save output.json.\n"
)

_VALID_REWRITE = (
    "---\n"
    "name: ticket-router\n"
    "model: claude-haiku-4-5\n"
    "---\n"
    "\n"
    "# Ticket Router\n"
    "\n"
    "## Deterministic fields (computed, not reasoned)\n"
    "\n"
    "The `category` field is deterministic. Write this to `helper.py`, run "
    "`python helper.py < ticket.txt`, and use the returned `category` verbatim — "
    "do not reason about it:\n"
    "\n"
    "```python\n"
    "import sys, json\n"
    "\n"
    "def compute_deterministic_fields(input_text: str) -> dict:\n"
    "    t = input_text.lower()\n"
    "    if 'renew' in t:\n"
    "        return {'category': 'renewal_risk'}\n"
    "    if 'log in' in t or 'password' in t:\n"
    "        return {'category': 'account_access'}\n"
    "    if 'crash' in t or 'stack trace' in t:\n"
    "        return {'category': 'bug_report'}\n"
    "    return {'category': 'general'}\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    print(json.dumps(compute_deterministic_fields(sys.stdin.read())))\n"
    "```\n"
    "\n"
    "## Process\n"
    "\n"
    "1. Read the ticket.\n"
    "2. Classify `team` and `priority` by reasoning; use the helper for `category`.\n"
    "3. Save output.json.\n"
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


_SAMPLES = [
    ("ticket_001.txt", "Subject: renewal failing. Our auto-renew is Friday.", {"category": "renewal_risk"}),
    ("ticket_002.txt", "Subject: can't log in, password reset not working.", {"category": "account_access"}),
    ("ticket_003.txt", "Subject: app crashes on export, stack trace attached.", {"category": "bug_report"}),
]


def _d005_finding(
    *,
    classification: str = "partial",
    stable: tuple[str, ...] = ("category",),
    n_examples: int = 3,
    break_first_input: bool = False,
    drop_corpus_keys: bool = False,
    empty_evidence: bool = False,
) -> Finding:
    ev: list[dict] = []
    if not empty_evidence:
        for i, (fn, txt, vals) in enumerate(_SAMPLES[:n_examples]):
            entry: dict = {
                "input_filename": fn,
                "trace_ref": f"run_{i + 1:03d}.jsonl",
                "n_replays": 3,
                "input_text": "" if (break_first_input and i == 0) else txt,
                "stable_fields": sorted(vals),
                "stable_values": dict(vals),
                "representative_output": dict(vals) | {"team": "support", "priority": "high"},
                "full_output_identical": False,
            }
            if i == 0 and not drop_corpus_keys:
                entry = entry | {
                    "classification": classification,
                    "stable_fields_corpuswide": list(stable),
                    "field_universe": ["team", "priority", "category"],
                }
            ev.append(entry)
    return Finding(
        finding_id="skopt-2026-05-11-d005-ticket_router-001",
        detector_id="D005",
        skill_id="ticket_router",
        category="deterministic_steps",
        observed_pattern="category is identical across replays",
        evidence=tuple(ev),
        estimated_cost_pct=-20.0,
        estimated_latency_pct=-16.7,
        quality_risk="medium",
        occurrences=n_examples,
    )


def test_rewriter_receives_stable_fields_and_examples() -> None:
    llm = _StubLLM(response=_VALID_REWRITE)
    proposal = propose_step_determinize(_d005_finding(), current_skill_text=_SKILL_TEXT, llm_client=llm)
    assert proposal is not None
    assert "category" in llm.last_user
    assert "renewal_risk" in llm.last_user            # a stable value made it into the evidence
    assert "auto-renew is Friday" in llm.last_user    # the example input text made it in
    assert "identical across every captured replay" in llm.last_user
    assert "ticket_001.txt" in llm.last_user


def test_proposal_carries_full_file_patch_tier2_step_determinize() -> None:
    llm = _StubLLM(response=_VALID_REWRITE)
    proposal = propose_step_determinize(_d005_finding(), current_skill_text=_SKILL_TEXT, llm_client=llm)
    assert proposal is not None
    assert proposal.tier == "2"
    assert proposal.mutation_type == "step_determinize"
    assert proposal.patch.target_relative_path == "SKILL.md"
    assert proposal.patch.full_file is True
    assert proposal.patch.before_text == _SKILL_TEXT
    assert proposal.patch.after_text == _VALID_REWRITE
    assert "category" in proposal.patch.description
    assert "partial" in proposal.patch.description


def test_preamble_is_stripped_before_validation() -> None:
    llm = _StubLLM(response="Sure — here's the rewrite:\n\n" + _VALID_REWRITE)
    proposal = propose_step_determinize(_d005_finding(), current_skill_text=_SKILL_TEXT, llm_client=llm)
    assert proposal is not None
    assert proposal.patch.after_text.startswith("---")
    assert "Sure" not in proposal.patch.after_text


def test_returns_none_when_llm_raises() -> None:
    llm = _StubLLM(raise_error=True)
    assert propose_step_determinize(_d005_finding(), current_skill_text=_SKILL_TEXT, llm_client=llm) is None


def test_returns_none_when_response_lacks_frontmatter() -> None:
    llm = _StubLLM(response="this is just prose, not a SKILL.md")
    assert propose_step_determinize(_d005_finding(), current_skill_text=_SKILL_TEXT, llm_client=llm) is None


def test_returns_none_on_noop_rewrite() -> None:
    llm = _StubLLM(response=_SKILL_TEXT)  # identical to current → no-op
    assert propose_step_determinize(_d005_finding(), current_skill_text=_SKILL_TEXT, llm_client=llm) is None


def test_returns_none_when_no_stable_fields() -> None:
    # No corpus-wide stable fields and no per-input fallback → nothing to determinize.
    f = Finding(
        finding_id="skopt-2026-05-11-d005-x-001", detector_id="D005", skill_id="ticket_router",
        category="deterministic_steps", observed_pattern="",
        evidence=(
            {"input_filename": "t1.txt", "input_text": "blah", "stable_fields": [],
             "stable_values": {}, "classification": "partial",
             "stable_fields_corpuswide": [], "field_universe": ["a"]},
            {"input_filename": "t2.txt", "input_text": "blah", "stable_fields": [],
             "stable_values": {}},
        ),
        estimated_cost_pct=0.0, estimated_latency_pct=0.0, quality_risk="medium", occurrences=2,
    )
    llm = _StubLLM(response=_VALID_REWRITE)
    assert propose_step_determinize(f, current_skill_text=_SKILL_TEXT, llm_client=llm) is None
    assert llm.last_user == ""  # no LLM call burned


def test_returns_none_when_evidence_empty() -> None:
    llm = _StubLLM(response=_VALID_REWRITE)
    assert propose_step_determinize(_d005_finding(empty_evidence=True), current_skill_text=_SKILL_TEXT, llm_client=llm) is None
    assert llm.last_user == ""


def test_returns_none_when_too_few_usable_examples() -> None:
    # n_examples=2, but the first one's input_text is blanked → only 1 usable → shown < 2.
    llm = _StubLLM(response=_VALID_REWRITE)
    f = _d005_finding(n_examples=2, break_first_input=True)
    assert propose_step_determinize(f, current_skill_text=_SKILL_TEXT, llm_client=llm) is None
    assert llm.last_user == ""
