"""Tier-2 rewriter (pairs with D005): embed a deterministic Python helper for
the fields D005 found stable and rewrite SKILL.md to use it for them. The
``full`` → standalone ``optimized.py`` (drop SKILL.md) path is deliberately not here.
"""
from __future__ import annotations

import json

from skill_optimizer.domain.mutations._rewriter_io import (
    is_plausible_skill_md,
    strip_preamble_to_frontmatter,
)
from skill_optimizer.domain.types import Finding, Patch, Proposal
from skill_optimizer.ports.llm_client import LLMClient, LLMClientError

_REWRITER_SYSTEM_PROMPT = """\
You are a SKILL.md rewriter for a skill optimizer.

Your job: given evidence that some of a skill's output fields are produced
DETERMINISTICALLY (identical across every captured replay of the same input),
rewrite the SKILL.md so those fields are computed by an embedded Python helper
instead of by the model's reasoning. Every other (non-deterministic) field stays
LLM-driven, exactly as before.

WHY: a field that is always the same for the same input is a rule, not a
judgement. Routing it through the LLM each run wastes tokens and adds variance.
Write the rule down as code, once.

REQUIRED CHANGES:
- Add a section titled "## Deterministic fields (computed, not reasoned)" that:
  - Names the field(s) being made deterministic.
  - Contains ONE fenced ```python block defining a function
    `compute_deterministic_fields(input_text: str) -> dict` that returns a dict
    containing exactly those field(s) and nothing else.
  - INFERS THE RULE from the examples — keyword matches, patterns, structure.
    Do NOT hardcode the specific example inputs or outputs; the helper is
    verified on inputs it has not seen, so it must generalize.
  - Instructs the model to: write that code to `helper.py`, run
    `python helper.py < <input file>`, and use the returned values VERBATIM for
    those fields. The model must NOT re-derive those fields by reasoning. (The
    helper should read stdin and `print(json.dumps(...))`.)
- Leave UNCHANGED: every instruction for the remaining fields, the process for
  them, the output schema (the model still emits ALL fields — some now come from
  the helper), and the rest of the skill.

PRESERVE VERBATIM:
- The YAML frontmatter (between --- markers) — name, description, model
- The H1 title
- The output schema block
- Required output filenames and paths
- All guidance for the non-deterministic fields

OUTPUT REQUIREMENTS — STRICT:
- Your response MUST begin with the exact characters `---` (the frontmatter open).
- No preamble, apology, or commentary before the first `---`. No code fences
  wrapping the whole file. Just the raw SKILL.md text, ready to write to disk.
- Return the COMPLETE rewritten SKILL.md.
"""

_MAX_EXAMPLES = 8
_INPUT_CLIP = 1500


def propose_step_determinize(
    finding: Finding,
    current_skill_text: str,
    llm_client: LLMClient,
    model: str = "claude-sonnet-4-6",
) -> Proposal | None:
    """Rewrite SKILL.md to compute D005's stable fields with an embedded helper.
    None on no stable fields, malformed evidence, transport failure, or a no-op rewrite."""
    evidence_block = _format_evidence_for_rewriter(finding)
    if not evidence_block:
        return None

    user = (
        f"<EVIDENCE>\n{evidence_block}\n</EVIDENCE>\n\n"
        f"<CURRENT_SKILL_MD>\n{current_skill_text}\n</CURRENT_SKILL_MD>\n\n"
        f"Rewrite the SKILL.md so the deterministic field(s) shown in the evidence "
        f"are computed by an embedded Python helper that infers the general rule "
        f"from the examples (never by hardcoding them), and the model uses the "
        f"helper's values verbatim for those fields. Keep everything else "
        f"unchanged. Output ONLY the rewritten SKILL.md content, no explanations "
        f"or fences."
    )
    try:
        rewritten = llm_client.complete(system=_REWRITER_SYSTEM_PROMPT, user=user, model=model)
    except LLMClientError:
        return None

    rewritten = strip_preamble_to_frontmatter(rewritten)
    if not is_plausible_skill_md(rewritten):
        return None
    if rewritten.strip() == current_skill_text.strip():
        return None  # rewriter produced a no-op

    stable = _stable_fields(finding)
    patch = Patch(
        target_relative_path="SKILL.md",
        before_text=current_skill_text,
        after_text=rewritten,
        description=(
            f"LLM rewrite embedding a deterministic helper for field(s) "
            f"[{', '.join(stable)}] (D005 classification: {_classification(finding)})"
        ),
        full_file=True,
    )
    return Proposal(
        proposal_id=f"{finding.finding_id}-prop",
        finding=finding,
        patch=patch,
        tier="2",
        mutation_type="step_determinize",
    )


def _classification(finding: Finding) -> str:
    if not finding.evidence:
        return "partial"
    return finding.evidence[0].get("classification") or "partial"


def _stable_fields(finding: Finding) -> list[str]:
    if not finding.evidence:
        return []
    ev0 = finding.evidence[0]
    fields = ev0.get("stable_fields_corpuswide") or ev0.get("stable_fields") or []
    return [str(f) for f in fields]


def _format_evidence_for_rewriter(finding: Finding) -> str:
    stable = _stable_fields(finding)
    if not stable or not finding.evidence:
        return ""
    lines = [
        f"The D005 determinism detector found these output field(s) identical "
        f"across every captured replay of every input: {', '.join(stable)}.",
        "Examples (input → the deterministic value of each stable field):",
    ]
    shown = 0
    for entry in finding.evidence:
        if not isinstance(entry, dict):
            continue
        inp = entry.get("input_text")
        vals = entry.get("stable_values")
        if not isinstance(inp, str) or not inp or not isinstance(vals, dict):
            continue
        wanted = {k: vals.get(k) for k in stable if k in vals}
        if not wanted:
            continue
        shown += 1
        lines.append("")
        lines.append(f"  --- input ({entry.get('input_filename', '?')}) ---")
        lines.append(_indent(_clip(inp, _INPUT_CLIP), "  | "))
        lines.append("  --- deterministic values for this input ---")
        lines.append(f"  {json.dumps(wanted, ensure_ascii=False)}")
        if shown >= _MAX_EXAMPLES:
            break
    if shown < 2:
        return ""
    lines.append("")
    lines.append(
        "Infer the GENERAL RULE mapping an input to these field values and "
        "express it as Python in `compute_deterministic_fields`. Do NOT enumerate "
        "these specific inputs — the helper is run on inputs it has not seen."
    )
    return "\n".join(lines)


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + " …[clipped]"


