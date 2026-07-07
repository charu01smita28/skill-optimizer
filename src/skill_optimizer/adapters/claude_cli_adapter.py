"""ClaudeCliAdapter — LLMClient implementation via subprocess to the local `claude` CLI.

Authenticates via Claude Team login (CLAUDE_CONFIG_DIR), no Anthropic API key
required. Same auth path as ``scripts/capture_traces.py``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from skill_optimizer.ports.llm_client import LLMClient, LLMClientError


class ClaudeCliAdapter:
    """LLMClient adapter shelling out to the local ``claude`` CLI.

    Constructor resolves the ``claude`` binary on PATH and validates env once.
    Each ``complete()`` call spawns a fresh subprocess — no session reuse.
    """

    def __init__(self, config_dir: str | None = None, timeout_s: int = 60) -> None:
        self._config_dir = config_dir or os.environ.get("CLAUDE_CONFIG_DIR")
        self._timeout_s = timeout_s
        self._claude_path = shutil.which("claude")
        if not self._claude_path:
            raise LLMClientError("`claude` CLI not found on PATH")

    def complete(self, system: str, user: str, model: str = "claude-haiku-4-5") -> str:
        # Combine system + user into a single -p prompt; the CLI uses
        # --append-system-prompt for system instruction layering.
        env = os.environ.copy()
        if self._config_dir:
            env["CLAUDE_CONFIG_DIR"] = self._config_dir
        # Pop API keys so the CLI uses Team login from CLAUDE_CONFIG_DIR.
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)

        cmd = [
            self._claude_path,
            "--model", model,
            "--append-system-prompt", system,
            "--allowedTools", "",   # judge call — no tools needed
            "--output-format", "json",
            "-p", user,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                timeout=self._timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise LLMClientError(f"`claude` CLI timeout after {self._timeout_s}s")

        if result.returncode != 0:
            raise LLMClientError(
                f"`claude` CLI failed (rc={result.returncode}): "
                f"{result.stderr.strip()[:300]}"
            )

        # `--output-format json` produces {"result": "...", "usage": {...}, ...}.
        try:
            parsed = json.loads(result.stdout)
            return str(parsed.get("result", "")).strip()
        except json.JSONDecodeError:
            return result.stdout.strip()
