"""Tests for ``skill_md`` — the SKILL.md frontmatter readers and the generic
replay-prompt builder. ``read_primary_fields`` feeds the verifier's three-tier
equivalence-field resolver; ``read_input_glob`` / ``read_summary_field`` /
``build_replay_prompt`` feed the capture pipeline + the verifier replay."""
from __future__ import annotations

from pathlib import Path

from skill_optimizer.skill_md import (
    DEFAULT_INPUT_GLOB,
    build_replay_prompt,
    read_input_glob,
    read_primary_fields,
    read_summary_field,
)


def _write_skill(tmp_path: Path, body: str) -> Path:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(body)
    return skill_dir


def test_reads_quoted_list(tmp_path: Path) -> None:
    s = _write_skill(tmp_path, '---\nname: x\nprimary_fields: ["valid"]\n---\n# x\n')
    assert read_primary_fields(s) == ("valid",)


def test_reads_multi_field_list(tmp_path: Path) -> None:
    s = _write_skill(
        tmp_path, '---\nprimary_fields: ["team", "priority", "category"]\n---\n# x\n',
    )
    assert read_primary_fields(s) == ("team", "priority", "category")


def test_reads_unquoted_list(tmp_path: Path) -> None:
    s = _write_skill(tmp_path, "---\nprimary_fields: [team, priority]\n---\n# x\n")
    assert read_primary_fields(s) == ("team", "priority")


def test_returns_none_when_absent(tmp_path: Path) -> None:
    s = _write_skill(tmp_path, "---\nname: x\nmodel: m\n---\n# body\n")
    assert read_primary_fields(s) is None


def test_returns_none_when_empty(tmp_path: Path) -> None:
    s = _write_skill(tmp_path, "---\nprimary_fields:\n---\n")
    assert read_primary_fields(s) is None


def test_returns_none_when_skill_md_missing(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    assert read_primary_fields(skill_dir) is None


def test_ignores_primary_fields_outside_frontmatter(tmp_path: Path) -> None:
    s = _write_skill(tmp_path, '---\nname: x\n---\nprimary_fields: ["nope"]\n')
    assert read_primary_fields(s) is None


# ---------- read_input_glob ----------------------------------------------------

def test_input_glob_quoted(tmp_path: Path) -> None:
    s = _write_skill(tmp_path, '---\ninput_glob: "ticket_*.txt"\n---\n# x\n')
    assert read_input_glob(s) == "ticket_*.txt"


def test_input_glob_unquoted(tmp_path: Path) -> None:
    s = _write_skill(tmp_path, "---\ninput_glob: invoice_*.json\n---\n# x\n")
    assert read_input_glob(s) == "invoice_*.json"


def test_input_glob_defaults_to_star_when_absent(tmp_path: Path) -> None:
    s = _write_skill(tmp_path, "---\nname: x\n---\n# x\n")
    assert read_input_glob(s) == DEFAULT_INPUT_GLOB == "*"


def test_input_glob_defaults_to_star_when_skill_md_missing(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    assert read_input_glob(skill_dir) == "*"


# ---------- read_summary_field -------------------------------------------------

def test_summary_field_uses_explicit_declaration(tmp_path: Path) -> None:
    s = _write_skill(
        tmp_path,
        '---\nprimary_fields: ["a", "b"]\nsummary_field: "b"\n---\n# x\n',
    )
    assert read_summary_field(s) == "b"


def test_summary_field_defaults_to_first_primary_field(tmp_path: Path) -> None:
    s = _write_skill(tmp_path, '---\nprimary_fields: ["valid", "computed"]\n---\n# x\n')
    assert read_summary_field(s) == "valid"


def test_summary_field_returns_none_when_neither_declared(tmp_path: Path) -> None:
    s = _write_skill(tmp_path, "---\nname: x\n---\n# x\n")
    assert read_summary_field(s) is None


# ---------- build_replay_prompt -----------------------------------------------

def test_build_replay_prompt_for_file_input(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    sample = skill_dir / "sample_inputs"
    sample.mkdir(parents=True)
    (sample / "ticket_001.txt").write_text("body")
    prompt = build_replay_prompt(skill_dir, "ticket_001.txt")
    assert "sample_inputs/ticket_001.txt," in prompt
    assert "sample_inputs/ticket_001.txt/," not in prompt


def test_build_replay_prompt_for_directory_bundle_appends_slash(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    bundle = skill_dir / "sample_inputs" / "q3_2026"
    bundle.mkdir(parents=True)
    (bundle / "data.json").write_text("{}")
    prompt = build_replay_prompt(skill_dir, "q3_2026")
    assert "sample_inputs/q3_2026/," in prompt
