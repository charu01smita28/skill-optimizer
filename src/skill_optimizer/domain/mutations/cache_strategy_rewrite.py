"""Tier-2 LLM rewriter (pairs with D006): rewrites SKILL.md to skip or guard recurring install/download work."""
from __future__ import annotations

from skill_optimizer.domain.mutations._rewriter_io import (
    is_plausible_skill_md,
    strip_preamble_to_frontmatter,
)
from skill_optimizer.domain.types import Finding, Patch, Proposal
from skill_optimizer.ports.llm_client import LLMClient, LLMClientError


_REWRITER_SYSTEM_PROMPT = """\
You are a SKILL.md rewriter for a skill optimizer.

Your job: given evidence of a recurring environment-setup operation \
(install, download, fetch) that runs on every invocation, rewrite an existing \
SKILL.md so the environment work is either assumed pre-provisioned or guarded \
by a check-then-install pattern.

THE PATTERN YOU'RE FIXING:
The skill installs the same package or fetches the same resource every run. \
That cost is paid each invocation even when the environment already has it. \
A skill should either: (a) assume its dependencies are present in the runtime \
image and not install them, OR (b) check first and install only when missing.

REQUIRED CHANGES:
- Either remove the install/fetch step from the skill's process (when the \
  dependency belongs in the runtime image), OR
- Replace the unconditional install with a check-first pattern: \
  ``command -v X >/dev/null 2>&1 || pip install X`` style for tools, or \
  ``[ -f X ] || curl -o X URL`` style for resources.
- Add a brief "## Environment" section documenting the assumption (e.g., \
  "Requires Python 3.11+ with pandas pre-installed").
- Use directive language: "Do not install …", "Assume … is available", \
  "Check before installing …".

PRESERVE VERBATIM:
- The YAML frontmatter (between --- markers) — name, description, model
- Any JSON code blocks (output schemas)
- The H1 title
- Required output filenames and paths
- Existing process steps unrelated to the install — only modify the install path

OUTPUT REQUIREMENTS — STRICT:
- Your response MUST begin with the exact characters `---` (the frontmatter open).
- Anything before the first `---` line — preamble, apology, commentary — is a \
  BUG and contaminates the SKILL.md downstream parser. DO NOT include it.
- No code fences (no ```` ```markdown ```` wrapping). Just the raw SKILL.md text.
- Return the COMPLETE rewritten SKILL.md, ready to write to disk verbatim.
"""


def propose_cache_strategy_rewrite(
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
        f"Rewrite the SKILL.md so the recurring environment-setup operation(s) "
        f"shown in the evidence either assume pre-provisioning or use a "
        f"check-then-install pattern. Preserve the YAML frontmatter, any JSON "
        f"output schema, and the H1 verbatim. Output ONLY the rewritten "
        f"SKILL.md content, no explanations or fences."
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
        description="LLM rewrite of SKILL.md to remove or guard recurring environment-setup operations",
        full_file=True,
    )
    return Proposal(
        proposal_id=f"{finding.finding_id}-prop",
        finding=finding,
        patch=patch,
        tier="2",
        mutation_type="cache_strategy_rewrite",
    )


def _format_evidence_for_rewriter(finding: Finding) -> str:
    if not finding.evidence:
        return ""

    lines = [
        f"The D006 env-setup-repeat detector identified these recurring "
        f"install/download operations across {finding.occurrences} captured runs:"
    ]
    seen_any_pattern = False
    for entry in finding.evidence:
        if not isinstance(entry, dict):
            continue
        family = entry.get("family")
        target = entry.get("target")
        command_excerpt = entry.get("command_excerpt", "")
        if not family or target is None or not command_excerpt:
            continue
        seen_any_pattern = True
        lines.append("")
        lines.append(f"  Family:  {family}")
        lines.append(f"  Target:  {target}")
        lines.append(f"  Command: {command_excerpt}")
    if not seen_any_pattern:
        return ""
    lines.append("")
    lines.append(
        "For each pattern, the model ran the same install/download command "
        "across multiple invocations. Rewrite the SKILL.md so this work is "
        "either skipped (when the runtime image carries the dependency) or "
        "guarded by a check that runs the install only when the dependency is missing."
    )
    return "\n".join(lines)


