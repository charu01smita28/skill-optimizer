"""Tests for ``_read_skill_model`` — the SKILL.md ``model:`` reader that feeds
``ClaudeAgentOptions(model=...)``. Without this, ``model_swap`` is a no-op.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from skill_optimizer.runtime import _read_skill_model


def _write_skill(tmp_path: Path, body: str) -> Path:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(body)
    return skill_dir


def test_reads_model_when_declared(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        "---\nname: x\nmodel: claude-haiku-4-5\n---\n# body\n",
    )
    assert _read_skill_model(skill_dir) == "claude-haiku-4-5"


def test_returns_none_when_model_absent(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "---\nname: x\n---\n# body\n")
    assert _read_skill_model(skill_dir) is None


def test_returns_none_when_skill_md_missing(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    assert _read_skill_model(skill_dir) is None


def test_ignores_model_outside_frontmatter(tmp_path: Path) -> None:
    skill_dir = _write_skill(
        tmp_path,
        "---\nname: x\n---\nmodel: claude-haiku-4-5\n",
    )
    assert _read_skill_model(skill_dir) is None


def test_returns_none_for_empty_value(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "---\nmodel:\n---\n")
    assert _read_skill_model(skill_dir) is None
