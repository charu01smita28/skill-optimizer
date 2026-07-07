"""Preflight: ping the SDK before bulk runtime work; fail fast on expired OAuth."""
from __future__ import annotations

import os
import sys

from skill_optimizer.runtime import run_async_clean


async def _auth_ping_async() -> tuple[bool, str]:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    options = ClaudeAgentOptions(
        max_turns=1,
        allowed_tools=[],
        setting_sources=[],
    )

    iterator = query(prompt="Reply with exactly the word: pong", options=options)
    try:
        async for msg in iterator:
            if isinstance(msg, ResultMessage):
                if msg.is_error:
                    return False, f"is_error=True, subtype={msg.subtype}"
                return True, "ok"
        return False, "no ResultMessage received from SDK"
    except Exception as e:
        text = str(e)
        if any(needle in text.lower() for needle in ("auth", "login", "credential", "401")):
            return False, f"auth failure: {text}"
        return False, f"{type(e).__name__}: {text}"
    finally:
        try:
            await iterator.aclose()
        except Exception:
            pass


def auth_ping() -> tuple[bool, str]:
    return run_async_clean(_auth_ping_async())


def preflight_or_exit() -> None:
    """Print SDK auth status; sys.exit(1) on fail. Caller must set CLAUDE_CONFIG_DIR first."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "")
    print(f"preflight: pinging Claude Agent SDK (CLAUDE_CONFIG_DIR={config_dir or '<unset>'})...")

    ok, msg = auth_ping()
    if ok:
        print("preflight: auth ok\n")
        return

    print(f"preflight: FAIL — {msg}", file=sys.stderr)

    config_hint = config_dir or "~/.aklaude"
    print(
        "\nMost likely your OAuth session expired. To re-authenticate:\n"
        f"  CLAUDE_CONFIG_DIR={config_hint} claude\n"
        "  > /login\n"
        "  > /exit\n"
        "\nFor a deeper 6-step diagnostic, run:\n"
        "  python scripts/check.py",
        file=sys.stderr,
    )
    raise SystemExit(1)
