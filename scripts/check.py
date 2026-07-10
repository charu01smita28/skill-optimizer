
"""
Diagnostic: Running Python Agent SDK with the aklaude session

Run from repo root:  python scripts/check.py

Walks six checks in order, stops at the first failure:
  1. Python version
  2. CLAUDE_CONFIG_DIR exists (aklaude config dir)
  3. claude binary on PATH
  4. claude-agent-sdk installed
  5. Auth works (a trivial query returns a result)
  6. Tool use works (Read tool inside cwd)
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Point the SDK-spawned `claude` at the aklaude config dir; clear API-key envs
# (claude prefers ANTHROPIC_API_KEY over CLAUDE_CONFIG_DIR when both are set).
_CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.aklaude")).expanduser()
os.environ["CLAUDE_CONFIG_DIR"] = str(_CONFIG_DIR)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


def check(label):
    """Decorator-style helper: prints the label, runs fn, prints PASS/FAIL."""
    def decorator(fn):
        async def wrapper():
            print(f"[{label}] ... ", end="", flush=True)
            try:
                detail = await fn() if asyncio.iscoroutinefunction(fn) else fn()
                print(f"PASS  {detail or ''}")
                return True
            except Exception as e:
                print(f"FAIL  {type(e).__name__}: {e}")
                return False
        return wrapper
    return decorator


# --- 1. Python version ---
@check("1/6 python version")
def check_python():
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 10):
        raise RuntimeError(f"need >=3.10, found {major}.{minor}")
    return f"({major}.{minor})"


# --- 2. aklaude config dir ---
@check("2/6 CLAUDE_CONFIG_DIR exists")
def check_config_dir():
    if not _CONFIG_DIR.exists():
        raise RuntimeError(
            f"{_CONFIG_DIR} not found. Set CLAUDE_CONFIG_DIR or create ~/.aklaude "
            "and run `claude /login` inside it."
        )
    return f"({_CONFIG_DIR})"


# --- 3. claude binary ---
@check("3/6 claude binary on PATH")
def check_binary():
    path = shutil.which("claude")
    if not path:
        raise RuntimeError("`claude` not found. Install Claude Code first.")
    # Also check it actually runs
    try:
        out = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=10, check=True
        )
        version = out.stdout.strip() or out.stderr.strip()
        return f"({path}, {version})"
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"binary exists but `claude --version` failed: {e.stderr.strip()}")


# --- 3. SDK installed ---
@check("4/6 claude-agent-sdk import")
def check_sdk_import():
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        raise RuntimeError("not installed. Run: pip install claude-agent-sdk")
    return f"(version {getattr(claude_agent_sdk, '__version__', 'unknown')})"


# --- 4. Auth works (trivial query) ---
@check("5/6 auth + simple query")
async def check_auth():
    from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage

    options = ClaudeAgentOptions(
        max_turns=1,
        # No tools needed for a pure text reply
        allowed_tools=[],
        setting_sources=[],  # don't load user/project/local settings, keep this isolated
    )

    got_result = False
    got_text = False
    error_detail = None

    try:
        async for msg in query(
            prompt="Reply with exactly the word: pong",
            options=options,
        ):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if hasattr(block, "text") and block.text.strip():
                        got_text = True
            if isinstance(msg, ResultMessage):
                got_result = True
                if msg.is_error:
                    error_detail = f"is_error=True, subtype={msg.subtype}"
                break
    except Exception as e:
        # Surface the most common ones with hints
        name = type(e).__name__
        msg = str(e)
        if "CLINotFoundError" in name:
            raise RuntimeError("SDK can't find claude binary (unexpected, step 2 passed)")
        if "auth" in msg.lower() or "login" in msg.lower() or "credential" in msg.lower():
            raise RuntimeError(f"auth problem: {msg}. Try: claude /login")
        if "rate" in msg.lower() or "limit" in msg.lower():
            raise RuntimeError(f"rate limited: {msg}")
        raise

    if not got_result:
        raise RuntimeError("no ResultMessage received")
    if error_detail:
        raise RuntimeError(error_detail)
    if not got_text:
        raise RuntimeError("ResultMessage ok but no assistant text — odd, but auth probably fine")
    return "(round-trip ok)"


# --- 5. Tool use works ---
@check("6/6 tool use (Read)")
async def check_tool_use():
    from claude_agent_sdk import (
        query, ClaudeAgentOptions,
        AssistantMessage, ResultMessage, ToolUseBlock,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "marker.txt"
        test_file.write_text("the secret word is banana")

        options = ClaudeAgentOptions(
            cwd=tmpdir,
            allowed_tools=["Read"],
            permission_mode="acceptEdits",
            max_turns=3,
            setting_sources=[],
        )

        used_read = False
        final_text = ""

        async for msg in query(
            prompt=f"Read the file marker.txt in the current directory and tell me the secret word.",
            options=options,
        ):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock) and block.name == "Read":
                        used_read = True
                    if hasattr(block, "text"):
                        final_text += block.text
            if isinstance(msg, ResultMessage):
                if msg.is_error:
                    raise RuntimeError(f"result error: subtype={msg.subtype}")
                break

        if not used_read:
            raise RuntimeError("Claude never called Read tool")
        if "banana" not in final_text.lower():
            raise RuntimeError(f"Read worked but answer wrong. Got: {final_text[:120]!r}")
        return "(Read tool functional)"


async def main():
    print("=" * 60)
    print("Claude Code Python Agent SDK diagnostic")
    print("=" * 60)

    checks = [
        check_python,
        check_config_dir,
        check_binary,
        check_sdk_import,
        check_auth,
        check_tool_use,
    ]
    results = []
    for c in checks:
        ok = await c()
        results.append(ok)
        if not ok:
            break

    print("=" * 60)
    if all(results) and len(results) == len(checks):
        print("VERDICT: GREEN — SDK works with the aklaude auth.")
        print("You can build the eval harness with claude_agent_sdk.")
    elif len(results) >= 5 and not results[4]:
        print("VERDICT: AUTH ISSUE — SDK installed but can't talk to Claude.")
        print(f"Confirm `claude /login` was run with CLAUDE_CONFIG_DIR={_CONFIG_DIR}.")
        print("Also check seat type: subscription seat needs Claude Code access.")
    elif len(results) >= 4 and not results[3]:
        print("VERDICT: MISSING SDK — install with: pip install claude-agent-sdk")
    elif len(results) >= 3 and not results[2]:
        print("VERDICT: NO CLAUDE BINARY — install Claude Code first.")
        print("  See: https://code.claude.com/docs/en/quickstart")
    elif len(results) >= 2 and not results[1]:
        print(f"VERDICT: NO CONFIG DIR — create {_CONFIG_DIR} and run `claude /login`.")
    else:
        print("VERDICT: see failures above.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())