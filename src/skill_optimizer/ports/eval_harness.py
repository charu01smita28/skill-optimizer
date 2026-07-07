"""EvalHarness port: run a skill against a prompt, return a structured result.

The seam exists so the verifier can be unit-tested without spawning a real
SDK subprocess. ``adapters/claude_cli_eval_adapter.py`` is the production
implementation; tests pass a fake.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from skill_optimizer.runtime import SkillRunResult


class EvalHarness(Protocol):
    def run(
        self,
        skill_dir: Path,
        prompt: str,
        allowed_tools: tuple[str, ...] = ("Read", "Edit", "Write"),
        timeout_s: int = 240,
    ) -> SkillRunResult: ...


__all__ = ["EvalHarness", "SkillRunResult"]
