"""Production EvalHarness implementation: spawns the Claude Agent SDK in-process."""
from __future__ import annotations

from pathlib import Path

from skill_optimizer.runtime import SkillRunResult, run_skill


class ClaudeCliEvalAdapter:
    """EvalHarness backed by ``runtime.run_skill`` (Claude Agent SDK)."""

    def run(
        self,
        skill_dir: Path,
        prompt: str,
        allowed_tools: tuple[str, ...] = ("Read", "Edit", "Write"),
        timeout_s: int = 240,
    ) -> SkillRunResult:
        return run_skill(
            skill_dir=skill_dir,
            prompt=prompt,
            allowed_tools=allowed_tools,
            timeout_s=timeout_s,
        )
