"""Tier-2 LLM rewriter (pairs with D003): adds SKILL.md tool-usage guidance preventing recurring failures."""
from __future__ import annotations

from skill_optimizer.domain.mutations._rewriter_io import (
    is_plausible_skill_md,
    strip_preamble_to_frontmatter,
)
from skill_optimizer.domain.types import Finding, Patch, Proposal
from skill_optimizer.ports.llm_client import LLMClient, LLMClientError


_REWRITER_SYSTEM_PROMPT = """\
You are a SKILL.md rewriter for a skill optimizer.

Your job: given evidence of a recurring tool-call failure followed by a retry, \
rewrite an existing SKILL.md to add explicit guidance preventing the failure \
mode upfront.

THE PATTERN YOU'RE FIXING:
The skill experiences a tool call that fails (returns an error), then the model \
retries the same tool with a similar but corrected input. By the second \
invocation, the model has learned what to do — but the failed first attempt \
costs tokens and latency on every run. If the SKILL.md tells the model the \
right approach the first time, the failure stops happening.

REQUIRED CHANGES:
- Add a "## Tool Usage Guidance" section (or extend an existing one) that:
  - Names the tool
  - Describes the failure mode concretely (what input shape causes errors)
  - States the corrected approach clearly
- Use directive language: "Use X, not Y" / "Always include Z" / "Never call \
  TOOL with Y when Z is missing"

PRESERVE VERBATIM:
- The YAML frontmatter (between --- markers) — name, description, model
- Any JSON code blocks (output schemas)
- The H1 title
- Required output filenames and paths
- Existing process steps — only ADD guidance, do not remove existing instructions

OUTPUT REQUIREMENTS — STRICT:
- Your response MUST begin with the exact characters `---` (the frontmatter open).
- Anything before the first `---` line — preamble, apology, commentary — is a \
  BUG and contaminates the SKILL.md downstream parser. DO NOT include it.
- No code fences (no ```` ```markdown ```` wrapping). Just the raw SKILL.md text.
- Return the COMPLETE rewritten SKILL.md, ready to write to disk verbatim.
"""


def propose_tool_guidance_rewrite(
    finding: Finding,
    current_skill_text: str,
    llm_client: LLMClient,
    model: str = "claude-sonnet-4-6",
) -> Proposal | None:
    evidence_block = _format_evidence_for_rewriter(finding)
    if not evidence_block:
        return None

    user = (
        f"<EVIDENCE>\n{evidence_block}\n</EVIDENCE>\n\n"
        f"<CURRENT_SKILL_MD>\n{current_skill_text}\n</CURRENT_SKILL_MD>\n\n"
        f"Rewrite the SKILL.md to add a 'Tool Usage Guidance' section that "
        f"prevents the recurring failure shown in the evidence. Preserve the "
        f"YAML frontmatter, any JSON output schema, and the H1 verbatim. "
        f"Output ONLY the rewritten SKILL.md content, no explanations or fences."
    )

    try:
        rewritten = llm_client.complete(system=_REWRITER_SYSTEM_PROMPT, user=user, model=model)
    except LLMClientError:
        return None

    rewritten = strip_preamble_to_frontmatter(rewritten)
    if not is_plausible_skill_md(rewritten):
        return None

    patch = Patch(
        target_relative_path="SKILL.md",
        before_text=current_skill_text,
        after_text=rewritten,
        description="LLM rewrite of SKILL.md adding tool-usage guidance to prevent recurring failures",
        full_file=True,
    )
    return Proposal(
        proposal_id=f"{finding.finding_id}-prop",
        finding=finding,
        patch=patch,
        tier="2",
        mutation_type="tool_guidance_rewrite",
    )


def _format_evidence_for_rewriter(finding: Finding) -> str:
    if not finding.evidence:
        return ""

    lines = [
        f"The D003 tool-reliability detector identified these failure-retry "
        f"patterns across {finding.occurrences} captured runs:"
    ]
    seen_any_pattern = False
    for entry in finding.evidence:
        if not isinstance(entry, dict):
            continue
        tool = entry.get("tool")
        failed_input = entry.get("failed_input")
        retry_input = entry.get("retry_input")
        error_excerpt = entry.get("error_excerpt", "")
        if not tool or failed_input is None or retry_input is None:
            continue
        seen_any_pattern = True
        lines.append("")
        lines.append(f"  Tool:          {tool}")
        lines.append(f"  Failed input:  {failed_input}")
        lines.append(f"  Error message: {error_excerpt}")
        lines.append(f"  Retry input:   {retry_input}")
    if not seen_any_pattern:
        return ""
    lines.append("")
    lines.append(
        "For each pattern, the model called the tool with the failed input, got "
        "the error message shown, then succeeded with the retry input. Add SKILL.md "
        "guidance that prevents the failed input from being attempted in the first place."
    )
    return "\n".join(lines)


