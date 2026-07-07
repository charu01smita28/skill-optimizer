"""In-process skill execution via the Claude Agent SDK.

* system_prompt uses the ``claude_code`` preset with appended directives so
  Claude Code's built-in tool conventions stay active; we add a cwd anchor
  and the Anthropic-docs parallel-tool-use directive on top.
* ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN must be unset (SDK uses CLAUDE_CONFIG_DIR).
* ClaudeAgentOptions(model=...) read from SKILL.md frontmatter; otherwise model_swap is a no-op.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Coroutine, TypeVar


_T = TypeVar("_T")


class _AsyncioTeardownNoiseFilter(logging.Filter):
    """Suppress cosmetic logger noise from SDK subprocess teardown (transport + asyncgen finalizer race after loop close)."""
    _NOISE = (
        "that handles pid",
        "asynchronous generator is already running",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return not any(p in msg for p in self._NOISE)


logging.getLogger("asyncio").addFilter(_AsyncioTeardownNoiseFilter())


def run_async_clean(coro: Coroutine[None, None, _T]) -> _T:
    """Like ``asyncio.run`` but drains pending tasks + shuts down asyncgens before close."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()


PARALLEL_TOOL_USE_DIRECTIVE = """\
<use_parallel_tool_calls>
For maximum efficiency, whenever you perform multiple independent operations, \
invoke all relevant tools simultaneously rather than sequentially. Prioritize \
calling tools in parallel whenever possible. For example, when reading 3 files, \
run 3 tool calls in parallel to read all 3 files into context at the same time. \
When running multiple read-only commands like `ls` or `list_dir`, always run \
all of the commands in parallel. Err on the side of maximizing parallel tool \
calls rather than running too many tools sequentially.
</use_parallel_tool_calls>"""


@dataclass
class SkillRunResult:
    started_at: float          # epoch seconds; lets callers locate the JSONL trace
    elapsed_s: float
    status: str                # "ok" | "failed" | "timeout"
    is_error: bool = False
    num_turns: int | None = None
    error: str | None = None


def setup_aklaude_env(config_dir: Path) -> None:
    """Set ``CLAUDE_CONFIG_DIR`` and unset API-key envs. Call once at process start."""
    os.environ["CLAUDE_CONFIG_DIR"] = str(config_dir)
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


def run_skill(
    skill_dir: Path,
    prompt: str,
    allowed_tools: tuple[str, ...] = ("Read", "Edit", "Write", "Task"),
    timeout_s: int = 240,
    max_turns: int = 20,
) -> SkillRunResult:
    """Run a skill via the SDK. Caller removes stale output.json + reads it after."""
    return run_async_clean(
        _run_skill_async(
            skill_dir=skill_dir.resolve(),
            prompt=prompt,
            allowed_tools=allowed_tools,
            timeout_s=timeout_s,
            max_turns=max_turns,
        )
    )


def _read_skill_model(skill_dir: Path) -> str | None:
    from skill_optimizer.skill_md import read_model
    return read_model(skill_dir)


async def _run_skill_async(
    skill_dir: Path,
    prompt: str,
    allowed_tools: tuple[str, ...],
    timeout_s: int,
    max_turns: int,
) -> SkillRunResult:
    from claude_agent_sdk import ClaudeAgentOptions, query

    append_text = (
        f"You are working in the directory: {skill_dir}\n"
        "All file paths in user messages are absolute and correct as given. "
        "Use them verbatim. Do NOT prepend /root/ or guess alternative locations.\n"
        "\n"
        f"{PARALLEL_TOOL_USE_DIRECTIVE}"
    )

    options = ClaudeAgentOptions(
        cwd=str(skill_dir),
        system_prompt={"type": "preset", "preset": "claude_code", "append": append_text},
        allowed_tools=list(allowed_tools),
        permission_mode="acceptEdits",
        max_turns=max_turns,
        setting_sources=[],
        model=_read_skill_model(skill_dir),
    )

    started_at = time.time()
    is_error = False
    num_turns: int | None = None
    error_detail: str | None = None

    iterator = query(prompt=prompt, options=options)
    try:
        async with asyncio.timeout(timeout_s):
            async for msg in iterator:
                if type(msg).__name__ == "ResultMessage":
                    is_error = bool(getattr(msg, "is_error", False))
                    num_turns = getattr(msg, "num_turns", None)
                    if is_error:
                        error_detail = f"subtype={getattr(msg, 'subtype', None)}"
    except asyncio.TimeoutError:
        return SkillRunResult(
            started_at=started_at,
            elapsed_s=time.time() - started_at,
            status="timeout",
            error=f"exceeded {timeout_s}s",
        )
    except Exception as e:
        return SkillRunResult(
            started_at=started_at,
            elapsed_s=time.time() - started_at,
            status="failed",
            error=f"{type(e).__name__}: {e}",
        )
    finally:
        try:
            await iterator.aclose()
        except Exception:
            pass

    elapsed = time.time() - started_at
    if is_error:
        return SkillRunResult(
            started_at=started_at,
            elapsed_s=elapsed,
            status="failed",
            is_error=True,
            num_turns=num_turns,
            error=error_detail,
        )

    return SkillRunResult(started_at=started_at, elapsed_s=elapsed, status="ok", num_turns=num_turns)
