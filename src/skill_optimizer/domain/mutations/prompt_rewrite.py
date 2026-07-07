"""Tier-2 rewriter (pairs with D007): tighten a verbose SKILL.md — cut ornamental
prose, keep every load-bearing instruction. Single-shot; the verifier confirms
output is unchanged, or the patch is rejected.
"""
from __future__ import annotations

from skill_optimizer.domain.mutations._rewriter_io import (
    is_plausible_skill_md,
    strip_preamble_to_frontmatter,
)
from skill_optimizer.domain.types import Finding, Patch, Proposal
from skill_optimizer.ports.llm_client import LLMClient, LLMClientError

_REWRITER_SYSTEM_PROMPT = """\
You are a SKILL.md rewriter for a skill optimizer.

Your job: tighten an existing SKILL.md so it is shorter and sharper, WITHOUT
changing what the model executing the skill will produce. Cut filler; keep
substance.

WHAT TO CUT:
- Redundant explanations — anything the rules/schema already say once.
- Over-long preambles, throat-clearing, motivational prose.
- Repetitive step wording (collapse "do A. then do A again for B. then do A
  again for C." into a single parameterized instruction).
- Examples that don't add information beyond the rule they illustrate.

WHAT TO KEEP — every instruction the output depends on:
- All routing rules, enum definitions, thresholds, edge-case handling.
- The exact process steps (you may compress wording, never drop a step).
- All notes that change behavior on specific cases.

PRESERVE VERBATIM:
- The YAML frontmatter (between --- markers) — name, description, model.
- The H1 title.
- The output schema block — every field, every enum value, exactly.
- Required output filenames and paths.

OUTPUT REQUIREMENTS — STRICT:
- Your response MUST begin with the exact characters `---` (the frontmatter open).
- No preamble, apology, or commentary before the first `---`. No code fences
  wrapping the whole file. Just the raw SKILL.md text, ready to write to disk.
- Return the COMPLETE rewritten SKILL.md. It MUST be shorter than the original.
"""


def propose_prompt_rewrite(
    finding: Finding,
    current_skill_text: str,
    llm_client: LLMClient,
    model: str = "claude-sonnet-4-6",
) -> Proposal | None:
    """Tighten the SKILL.md. None on transport failure, an implausible response,
    a no-op, or a rewrite that isn't actually shorter.
    """
    user = (
        f"<SECTION_SIZES>\n{_format_sections(finding)}\n</SECTION_SIZES>\n\n"
        f"<CURRENT_SKILL_MD>\n{current_skill_text}\n</CURRENT_SKILL_MD>\n\n"
        f"Tighten this SKILL.md per the rules above — shorter and sharper, same "
        f"behavior. Preserve the frontmatter, H1, and output schema verbatim. "
        f"Output ONLY the rewritten SKILL.md, no explanations or fences."
    )
    try:
        rewritten = llm_client.complete(system=_REWRITER_SYSTEM_PROMPT, user=user, model=model)
    except LLMClientError:
        return None

    rewritten = strip_preamble_to_frontmatter(rewritten)
    if not is_plausible_skill_md(rewritten):
        return None
    if rewritten.strip() == current_skill_text.strip():
        return None  # no-op
    if len(rewritten) >= len(current_skill_text):
        return None  # not actually a tightening

    patch = Patch(
        target_relative_path="SKILL.md",
        before_text=current_skill_text,
        after_text=rewritten,
        description=(
            f"LLM rewrite tightening SKILL.md "
            f"({len(current_skill_text)} → {len(rewritten)} chars)"
        ),
        full_file=True,
    )
    return Proposal(
        proposal_id=f"{finding.finding_id}-prop",
        finding=finding,
        patch=patch,
        tier="2",
        mutation_type="prompt_rewrite",
    )


def _format_sections(finding: Finding) -> str:
    rows = []
    for e in finding.evidence:
        if isinstance(e, dict) and "heading" in e and "chars" in e:
            rows.append(f"  {e['heading']}: {e['chars']} chars")
    return "\n".join(rows) if rows else "  (section breakdown unavailable)"

