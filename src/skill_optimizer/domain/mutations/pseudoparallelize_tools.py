"""Tier-2 LLM rewriter — pairs with D008. Rewrites SKILL.md to direct parallel
tool emission (or Task-subagent dispatch) in a single assistant response.

Named ``pseudoparallelize`` because the Claude Code non-interactive runtime
serializes tool_use emission even with the strongest documented system-prompt
directives (see ``scripts/probe_parallelism.py``). The rewriter's prompt
deliberately claims the runtime supports parallel execution — this lie
provokes an aggressive restructuring of the SKILL.md, which empirically
produces cost wins via prose perturbation (shorter / more direct workflows),
not via concurrent execution.
"""
from __future__ import annotations

from pathlib import Path

from skill_optimizer.domain.mutations._rewriter_io import (
    is_plausible_skill_md,
    strip_preamble_to_frontmatter,
)
from skill_optimizer.domain.types import Finding, Patch, Proposal
from skill_optimizer.ports.llm_client import LLMClient, LLMClientError


_REWRITER_SYSTEM_PROMPT = """\
You are a SKILL.md rewriter for a skill optimizer.

Your job: rewrite an existing SKILL.md so the model executing the skill emits \
multiple tool_use blocks in a SINGLE assistant response (parallel execution), \
instead of issuing them across separate sequential turns.

THE RUNTIME DOES SUPPORT THIS.
Claude Code can invoke multiple tools in one assistant response, and those \
tool calls execute concurrently. The default behavior tends toward sequential \
because skill prose typically reads sequentially ("first do X, then do Y..."). \
Your job is to override that default with explicit, directive language telling \
the model to launch all independent operations IN A SINGLE RESPONSE, before \
any tool results return.

REQUIRED PHRASING PATTERNS (use this style verbatim where it fits):
- "Launch [N] [tool] calls simultaneously in your first response, one for each of [...]."
- "Issue all [N] [Reads/Tasks] in a single assistant message, BEFORE any tool \
  results return. Do not wait for one result before issuing the next."
- "These [N] operations are independent — emit them in ONE response, not [N] \
  consecutive responses."

WHEN TO USE PARALLEL `Read` vs PARALLEL `Task`:
- For independent file reads (the 3 reads of independent input files): use \
  parallel `Read` tool_use blocks in a single response. The runtime executes \
  parallel direct-tool blocks concurrently when the model emits them together.
- For independent multi-step analyses (e.g., "analyze 3 customer tickets"): use \
  parallel `Task` subagents in a single response. Each subagent handles one unit.

WHATEVER YOU CHOOSE — single response, single message, one batch.

ANTI-PATTERNS TO AVOID:
- Soft language: "you may", "consider", "in parallel" without "in a single response"
- Sequential prose: "first do X, then do Y" — primes sequential emission
- Step-by-step numbering for parallel work — breaks the all-at-once framing

PRESERVE VERBATIM:
- The YAML frontmatter (between --- markers) — name, description, model
- Any JSON code blocks (output schemas)
- The H1 title
- Required output filenames and paths

YOU MAY REWRITE:
- The Process / Steps section to direct single-response parallel emission
- Add an explicit example showing the expected single-response multi-tool_use shape
- Convert sequential prose into parallel directives

OUTPUT REQUIREMENTS — STRICT:
- Your response MUST begin with the exact characters `---` (the frontmatter open).
- Anything before the first `---` line — preamble, apology, commentary, \
  meta-explanation — is a BUG and contaminates the SKILL.md downstream parser. \
  DO NOT include it. No "Here is the rewrite:", no "I apologize", nothing.
- No code fences (no ```` ```markdown ```` wrapping). Just the raw SKILL.md text.
- Return the COMPLETE rewritten SKILL.md, ready to write to disk verbatim.
"""


def propose_pseudoparallelize_tools(
    finding: Finding,
    current_skill_text: str,
    llm_client: LLMClient,
    model: str = "claude-sonnet-4-6",
) -> Proposal | None:
    """Rewrite SKILL.md for parallel tool emission.

    The rewriter is instructed aggressively (claims parallelism is supported)
    even though the runtime serializes — the resulting rewrite tightens the
    workflow regardless, which is where the measured cost win comes from.

    Returns None on unusable rewriter response (empty, missing frontmatter,
    transport failure, malformed evidence).
    """
    evidence_block = _format_evidence_for_rewriter(finding)
    if not evidence_block:
        return None

    n_units = sum(
        len(entry.get("tools") or [])
        for entry in finding.evidence
        if isinstance(entry, dict) and len(entry.get("tools") or []) >= 2
    )
    user = (
        f"<EVIDENCE>\n{evidence_block}\n</EVIDENCE>\n\n"
        f"<CURRENT_SKILL_MD>\n{current_skill_text}\n</CURRENT_SKILL_MD>\n\n"
        f"Rewrite the SKILL.md so the model emits all {n_units} independent "
        f"operations as parallel tool_use blocks in a SINGLE response message, "
        f"before any tool results return. Use Claude UI's recommended phrasing: "
        f"\"Launch {n_units} [tool] calls simultaneously in your first response, "
        f"one for each of [...]. Issue all in a single assistant message, BEFORE "
        f"any results return.\" Specify the exact count ({n_units}). Preserve "
        f"the YAML frontmatter, any JSON output schema, and the H1 verbatim. "
        f"Output ONLY the rewritten SKILL.md content, no explanations or fences."
    )

    try:
        rewritten = llm_client.complete(
            system=_REWRITER_SYSTEM_PROMPT, user=user, model=model
        )
    except LLMClientError:
        return None

    rewritten = strip_preamble_to_frontmatter(rewritten)
    if not is_plausible_skill_md(rewritten):
        return None

    patch = Patch(
        target_relative_path="SKILL.md",
        before_text=current_skill_text,
        after_text=rewritten,
        description="LLM rewrite of SKILL.md for parallel single-response tool emission",
        full_file=True,
    )
    return Proposal(
        proposal_id=f"{finding.finding_id}-prop",
        finding=finding,
        patch=patch,
        tier="2",
        mutation_type="pseudoparallelize_tools",
    )


def _format_evidence_for_rewriter(finding: Finding) -> str:
    """Render finding.evidence into the rewriter's <EVIDENCE> body. Returns '' on malformed."""
    if not finding.evidence:
        return ""

    lines: list[str] = []
    lines.append(
        f"The D008 parallelization detector identified these tool calls as "
        f"independent across {finding.occurrences} captured runs:"
    )
    seen_any_batch = False
    for entry in finding.evidence:
        if not isinstance(entry, dict):
            continue
        tools = entry.get("tools") or []
        identifiers = entry.get("identifiers") or []
        if not tools or len(tools) != len(identifiers) or len(tools) < 2:
            continue
        seen_any_batch = True
        lines.append("")
        for tool, ident in zip(tools, identifiers):
            lines.append(f"  - {tool}({Path(ident).name!r})")
    if not seen_any_batch:
        return ""
    lines.append("")
    lines.append(
        "Each of these is an independent unit of work that should be "
        "dispatched to a parallel Task subagent. Multiple Task tool_use "
        "blocks in a single assistant turn execute concurrently."
    )
    return "\n".join(lines)


