"""SKILL.md frontmatter reader + replay-prompt builder.

Single source of truth for the frontmatter keys our system reads (``model``,
``primary_fields``, ``input_glob``, ``summary_field``) and for the generic
prompt template the verifier and capture pipeline both replay against.
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_INPUT_GLOB = "*"
DEFAULT_OUTPUT_PATH = "output.json"


def read_model(skill_dir: Path) -> str | None:
    """Frontmatter ``model:`` value, or None when absent or empty."""
    return _frontmatter(skill_dir).get("model") or None


def read_primary_fields(skill_dir: Path) -> tuple[str, ...] | None:
    """Frontmatter ``primary_fields:`` value as a tuple of strings, or None if absent.

    Supports two YAML shapes:
        primary_fields: ["valid", "computed"]
        primary_fields: [valid, computed]
    """
    raw = _frontmatter(skill_dir).get("primary_fields")
    if not raw:
        return None
    return _parse_list(raw)


def read_input_glob(skill_dir: Path) -> str:
    """Frontmatter ``input_glob:`` value (quoted or bare), defaulting to ``*``.

    ``*`` matches every entry in ``sample_inputs/`` — files and directories alike.
    """
    raw = _frontmatter(skill_dir).get("input_glob")
    if not raw:
        return DEFAULT_INPUT_GLOB
    return raw.strip().strip('"').strip("'") or DEFAULT_INPUT_GLOB


def read_summary_field(skill_dir: Path) -> str | None:
    """Frontmatter ``summary_field:``, falling back to ``primary_fields[0]``.

    Used by the capture pipeline for the per-run console summary line. Returns
    None when neither is declared (capture prints ``<missing>``).
    """
    raw = _frontmatter(skill_dir).get("summary_field")
    if raw:
        return raw.strip().strip('"').strip("'") or None
    primary = read_primary_fields(skill_dir)
    return primary[0] if primary else None


def read_output_path(skill_dir: Path) -> str:
    """Frontmatter ``output_path:`` (relative to skill_dir), defaulting to ``output.json``.

    The skill is expected to write its structured answer to this path; the verifier
    extracts that file's content from the trace's Write tool call when comparing.
    """
    raw = _frontmatter(skill_dir).get("output_path")
    if not raw:
        return DEFAULT_OUTPUT_PATH
    return raw.strip().strip('"').strip("'") or DEFAULT_OUTPUT_PATH


def build_replay_prompt(
    skill_dir: Path,
    input_name: str,
    inputs_dir: Path | None = None,
    output_path: str | None = None,
) -> str:
    """Format the standard replay prompt for one input.

    Appends a trailing ``/`` when the input is a directory bundle so the model
    treats it as a directory to enumerate, not a file to read.

    ``inputs_dir`` defaults to ``<skill_dir>/sample_inputs``;
    ``output_path`` defaults to whatever ``read_output_path(skill_dir)`` returns.
    """
    inputs_dir = inputs_dir or (skill_dir / "sample_inputs")
    output_path = output_path or read_output_path(skill_dir)
    input_path = inputs_dir / input_name
    suffix = "/" if input_path.is_dir() else ""
    return (
        f"Read {skill_dir}/SKILL.md and the input at "
        f"{input_path}{suffix}, then follow the process in "
        f"{skill_dir}/SKILL.md and save the result as {skill_dir}/{output_path}."
    )


def _parse_list(raw: str) -> tuple[str, ...] | None:
    inner = raw.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    items = [f.strip().strip('"').strip("'") for f in inner.split(",")]
    items = [f for f in items if f]
    return tuple(items) if items else None


def _frontmatter(skill_dir: Path) -> dict[str, str]:
    """Parse the YAML frontmatter (between the first two ``---`` markers) as flat key→value."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return {}
    out: dict[str, str] = {}
    in_frontmatter = False
    for line in skill_md.read_text().splitlines():
        if line.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            break
        if not in_frontmatter:
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out
