"""preload_file Tier-A mutation — pairs with D001 (RedundantLookup).

D001 emits one consolidated Finding per skill, with one evidence entry per
detected pattern. This mutation reads ALL pattern entries and produces a
single directive listing every re-fetched input by basename.
"""
from __future__ import annotations

import re
from pathlib import Path

from skill_optimizer.domain.types import Finding, Patch, Proposal


_BASH_CAT_PATTERN = re.compile(r"^\s*cat\s+(\S+)\s*$")


def propose_preload_file(finding: Finding, current_skill_text: str) -> Proposal:
    """Tier-A: insert one directive citing every redundant-lookup pattern in the Finding.

    The directive uses basenames (not absolute paths) so it matches the voice
    of the SKILL.md instructions, which reference relative filenames.
    """
    directive = _build_directive(finding)

    body_h1 = _find_first_h1_line(current_skill_text)
    if body_h1 is None:
        raise ValueError("SKILL.md has no H1 heading — cannot anchor preload_file mutation")

    patch = Patch(
        target_relative_path="SKILL.md",
        before_text=body_h1,
        after_text=f"{directive}\n\n{body_h1}",
        description="Insert preload-file directive before first H1 in SKILL.md body",
    )
    return Proposal(
        proposal_id=f"{finding.finding_id}-prop",
        finding=finding,
        patch=patch,
        tier="1",
        mutation_type="preload_file",
    )


def _build_directive(finding: Finding) -> str:
    """Compose the inline directive from every pattern in the Finding's evidence."""
    if not finding.evidence:
        return _generic_fallback()

    file_targets: list[str] = []   # basenames, deduped, order-preserving
    other_tools: list[str] = []    # tools with non-extractable inputs
    seen_files: set[str] = set()
    seen_tools: set[str] = set()

    for entry in finding.evidence:
        tool = entry.get("tool_name") if isinstance(entry, dict) else None
        input_dict = entry.get("input") if isinstance(entry, dict) else None
        if not tool or not isinstance(input_dict, dict):
            continue
        file_path = _extract_file_path(tool, input_dict)
        if file_path:
            basename = Path(file_path).name
            if basename not in seen_files:
                seen_files.add(basename)
                file_targets.append(basename)
        elif tool not in seen_tools:
            seen_tools.add(tool)
            other_tools.append(tool)

    if not file_targets and not other_tools:
        return _generic_fallback()

    n_traces = finding.occurrences

    if file_targets:
        files_clause = _format_file_list(file_targets)
        if other_tools:
            tools_clause = ", ".join(f"`{t}`" for t in other_tools)
            tail = (
                f" The skill also re-invokes {tools_clause} with the same input — "
                f"cache and reuse those results too."
            )
        else:
            tail = ""
        return (
            f"> **Optimizer note (D001 — preload_file):** Across {n_traces} captured runs, "
            f"this skill repeatedly fetches {files_clause}. Tool results are cached per "
            f"session — read each of these files once at the start of your work and use the "
            f"cached content throughout; do not re-fetch the same file with different "
            f"`offset`/`limit` slices.{tail}"
        )

    tools_clause = ", ".join(f"`{t}`" for t in other_tools)
    return (
        f"> **Optimizer note (D001 — preload_file):** Across {n_traces} captured runs, "
        f"this skill calls {tools_clause} with the same input multiple times per run. "
        f"Tool results are cached per session — call each tool once with this input and "
        f"reuse the result; do not re-invoke."
    )


def _format_file_list(files: list[str]) -> str:
    if len(files) == 1:
        return f"`{files[0]}`"
    if len(files) == 2:
        return f"`{files[0]}` and `{files[1]}`"
    head = ", ".join(f"`{f}`" for f in files[:-1])
    return f"{head}, and `{files[-1]}`"


def _generic_fallback() -> str:
    return (
        "> **Optimizer note (D001 — preload_file):** "
        "Each `Read` (and other read-style tool) is cached for the session. "
        "Do not call the same tool with the same input more than once per session."
    )


def _extract_file_path(tool: str, input_dict: dict) -> str | None:
    """Recover the file path from the structured input on the Finding's evidence."""
    if tool == "Read":
        file_path = input_dict.get("file_path")
        return file_path if isinstance(file_path, str) else None
    if tool == "Bash":
        cmd = input_dict.get("command")
        if isinstance(cmd, str):
            match = _BASH_CAT_PATTERN.match(cmd)
            if match and not match.group(1).startswith("-"):
                return match.group(1)
    return None


def _find_first_h1_line(skill_text: str) -> str | None:
    in_frontmatter = False
    saw_frontmatter_open = False
    for line in skill_text.splitlines():
        if line.strip() == "---":
            if not saw_frontmatter_open:
                in_frontmatter = True
                saw_frontmatter_open = True
            else:
                in_frontmatter = False
            continue
        if in_frontmatter:
            continue
        if line.startswith("# "):
            return line
    return None
