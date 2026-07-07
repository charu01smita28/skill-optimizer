"""Tier-2 rewriter (D012): distill the re-derived script into ``helper.py`` +
rewrite SKILL.md to invoke it."""
from __future__ import annotations

import re

from skill_optimizer.domain.mutations._rewriter_io import (
    is_plausible_skill_md,
    strip_preamble_to_frontmatter,
)
from skill_optimizer.domain.types import Finding, Patch, Proposal
from skill_optimizer.ports.llm_client import LLMClient, LLMClientError

_REWRITER_SYSTEM_PROMPT = """\
You are a SKILL.md rewriter for a skill optimizer.

CONTEXT: a skill currently instructs the model to author a Python function from
scratch every run. The captured traces show the same function being re-derived
on every invocation — wasted tokens, re-rolled correctness. We want to lift
that function out into a persisted ``helper.py`` that SKILL.md just invokes.

YOUR TASK — emit TWO files, in this exact order:

1. ``helper.py`` — one clean, canonical Python file:
   - Distill the captured re-derivations into ONE function with the recurring
     name shown in the evidence.
   - Add an ``if __name__ == "__main__":`` block that:
     * reads ``sys.argv[1]`` as the path to a JSON input file,
     * calls the function on the parsed input,
     * writes the result to ``output.json`` (json.dumps, indent=2).
   - Pure stdlib only (``json``, ``sys``, ``decimal`` if needed). No external imports.
   - Infer the GENERAL RULE from the captured versions; do NOT hardcode the
     example inputs or outputs. The helper is run on inputs it hasn't seen.
   - No prints to stdout/stderr from the main block.

2. ``SKILL.md`` — a rewritten skill that invokes the helper:
   - Preserve the YAML frontmatter VERBATIM (name, description, model).
   - Preserve the H1 title and the output-schema block exactly.
   - Replace the "implement this function and apply it" instructions with a
     short Process section that says, in substance: run
     ``python helper.py sample_inputs/<input-file>``, then read ``output.json``.
     One or two short steps is enough. Do NOT include any Python code in
     SKILL.md — the code lives in helper.py.
   - Keep the skill in the same DOMAIN — what it does is unchanged; only HOW
     it produces the output changed.

OUTPUT FORMAT — STRICT:

<HELPER_PY>
...the complete helper.py source, no fences, no preamble...
</HELPER_PY>
<SKILL_MD>
...the complete rewritten SKILL.md, starting with --- frontmatter, no fences...
</SKILL_MD>

No text before the first tag, between the tags, or after the last tag.
"""

_MAX_EVIDENCE = 6
_MAX_CODE_PER_EVIDENCE = 6000


def propose_helper_extract(
    finding: Finding,
    current_skill_text: str,
    llm_client: LLMClient,
    model: str = "claude-sonnet-4-6",
) -> Proposal | None:
    """Extract the re-derived function into helper.py + rewrite SKILL.md to call it."""
    primary = _primary_function_name(finding)
    if not primary:
        return None
    evidence_block = _format_evidence_for_rewriter(finding, primary)
    if not evidence_block:
        return None

    user = (
        f"<RECURRING_FUNCTION_NAME>{primary}</RECURRING_FUNCTION_NAME>\n\n"
        f"<EVIDENCE>\n{evidence_block}\n</EVIDENCE>\n\n"
        f"<CURRENT_SKILL_MD>\n{current_skill_text}\n</CURRENT_SKILL_MD>\n\n"
        f"Emit the two files per the format above. The helper.py must define "
        f"`{primary}` and call it from the __main__ block on the JSON read from "
        f"sys.argv[1], writing output.json."
    )
    try:
        response = llm_client.complete(system=_REWRITER_SYSTEM_PROMPT, user=user, model=model)
    except LLMClientError:
        return None

    helper_code, rewritten_skill = _split_response(response)
    if helper_code is None or rewritten_skill is None:
        return None
    if not _is_plausible_helper(helper_code, primary):
        return None

    rewritten_skill = strip_preamble_to_frontmatter(rewritten_skill)
    if not is_plausible_skill_md(rewritten_skill):
        return None
    if rewritten_skill.strip() == current_skill_text.strip():
        return None  # no-op

    patch = Patch(
        target_relative_path="SKILL.md",
        before_text=current_skill_text,
        after_text=rewritten_skill,
        description=(
            f"LLM extraction of re-derived `{primary}` into helper.py + SKILL.md "
            f"rewritten to invoke it"
        ),
        full_file=True,
        new_files={"helper.py": helper_code},
    )
    return Proposal(
        proposal_id=f"{finding.finding_id}-prop",
        finding=finding,
        patch=patch,
        tier="2",
        mutation_type="helper_extract",
    )


_PRIMARY_RE = re.compile(r"`(\w+)\(")
_TAG_HELPER = re.compile(r"<HELPER_PY>\s*(.*?)\s*</HELPER_PY>", re.S)
_TAG_SKILL = re.compile(r"<SKILL_MD>\s*(.*?)\s*</SKILL_MD>", re.S)
_HAS_MAIN_GUARD = re.compile(r'if\s+__name__\s*==\s*[\'"]__main__[\'"]\s*:')


def _primary_function_name(finding: Finding) -> str | None:
    m = _PRIMARY_RE.search(finding.observed_pattern)
    return m.group(1) if m else None


def _split_response(text: str) -> tuple[str | None, str | None]:
    h = _TAG_HELPER.search(text)
    s = _TAG_SKILL.search(text)
    if not h or not s:
        return None, None
    helper = h.group(1).strip()
    skill = s.group(1)
    if not helper.endswith("\n"):
        helper += "\n"
    return helper, skill


def _is_plausible_helper(code: str, primary: str) -> bool:
    if not code or len(code) < 60:
        return False
    if not re.search(rf"^[ \t]*def[ \t]+{re.escape(primary)}\b", code, re.M):
        return False
    return bool(_HAS_MAIN_GUARD.search(code))


def _format_evidence_for_rewriter(finding: Finding, primary: str) -> str:
    n_shown = min(len(finding.evidence), _MAX_EVIDENCE)
    lines = [
        f"The recurring function is `{primary}`. Below are {n_shown} versions of it the model "
        f"authored across separate runs. Distill them into one canonical implementation; the "
        f"variation is mostly cosmetic (variable names, rounding-helper names, comment style)."
    ]
    shown = 0
    for entry in finding.evidence:
        if not isinstance(entry, dict):
            continue
        code = entry.get("code")
        if not isinstance(code, str) or not code:
            continue
        shown += 1
        ref = entry.get("trace_ref", "?")
        origin = entry.get("origin", "?")
        lines.append("")
        lines.append(f"--- {ref} (origin={origin}) ---")
        lines.append(code[:_MAX_CODE_PER_EVIDENCE])
        if shown >= _MAX_EVIDENCE:
            break
    if shown < 2:
        return ""
    return "\n".join(lines)
