"""Shared LLM-response validation for Tier-2 SKILL.md rewriters."""
from __future__ import annotations


def strip_preamble_to_frontmatter(text: str) -> str:
    """Drop text before the first ``---``; models sometimes prepend preamble."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "---":
            return "\n".join(lines[i:]) + ("\n" if text.endswith("\n") else "")
    return text


def is_plausible_skill_md(text: str) -> bool:
    """Structural sniff-test: frontmatter delimiters + an H1."""
    if not text or len(text) < 100 or text.count("---") < 2:
        return False
    in_frontmatter = False
    saw_open = False
    for line in text.splitlines():
        if line.strip() == "---":
            if not saw_open:
                in_frontmatter, saw_open = True, True
            else:
                in_frontmatter = False
            continue
        if not in_frontmatter and line.startswith("# "):
            return True
    return False
