"""SDK-based variant of inspect_trace.py.

Runs the ticket_router demo skill via claude_agent_sdk (no subprocess),
captures the structured message stream, and then checks whether the same
~/.aklaude/projects/<sanitized-cwd>/*.jsonl trace file gets written.

Goal: confirm whether dropping the CLI subprocess path costs us the JSONL
trace that the optimizer ingests, or whether the SDK produces an
equivalent file we can keep using.

Run from repo root:
    python scripts/inspect_trace_sdk.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Match inspect_trace.py / check.py: aklaude config dir, no API key.
_CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.aklaude")).expanduser()
os.environ["CLAUDE_CONFIG_DIR"] = str(_CONFIG_DIR)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# Reuse the existing analyzer so the JSONL side-by-side is apples-to-apples.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_trace import (  # noqa: E402
    analyze_trace,
    newest_jsonl_in,
    session_dir_for_cwd,
)

SKILL_DIR = Path(__file__).resolve().parent.parent / "demo" / "skills" / "ticket_router"
MAX_TURNS = 20


def build_prompt(skill_dir: Path, output_filename: str) -> str:
    # Bake absolute paths into the prompt. `./` wasn't strong enough — the model
    # has a learned bias toward /root/SKILL.md from Claude Code's sandboxed-skill
    # convention. Absolute paths give it nothing to guess.
    skill_md = skill_dir / "SKILL.md"
    sample_input = skill_dir / "sample_inputs" / "ticket_001.txt"
    output = skill_dir / output_filename
    return (
        f"Read {skill_md} and {sample_input}, then follow the process in "
        f"{skill_md} and save the result as {output}."
    )


async def run_sdk(run_cwd: Path, prompt: str) -> tuple[list, float]:
    from claude_agent_sdk import query, ClaudeAgentOptions

    # System prompt anchors the model in the real cwd and tells it not to guess
    # alternative paths. The model has a learned prior toward /root/SKILL.md
    # (Claude Code sandbox convention); we explicitly override it here.
    system_prompt = (
        f"You are working in the directory: {run_cwd}\n"
        "All file paths provided in user messages are absolute and correct as given. "
        "Use them verbatim. Do NOT prepend /root/ or guess alternative locations."
    )

    options = ClaudeAgentOptions(
        cwd=str(run_cwd),
        system_prompt=system_prompt,
        allowed_tools=["Read", "Edit", "Write"],
        permission_mode="acceptEdits",
        max_turns=MAX_TURNS,
        setting_sources=[],
    )

    started_at = time.time()
    messages: list = []
    async for msg in query(prompt=prompt, options=options):
        messages.append(msg)
    return messages, started_at


def summarize_sdk_stream(messages: list) -> None:
    print("\n=== SDK message stream ===")
    print(f"message count: {len(messages)}")

    type_counts = Counter(type(m).__name__ for m in messages)
    print(f"messages by type: {dict(type_counts)}")

    block_counts: Counter = Counter()
    tool_uses: list[tuple[str, str]] = []  # (tool_name, brief_input)
    last_assistant_text = ""

    for m in messages:
        content = getattr(m, "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            block_counts[type(block).__name__] += 1
            tool_name = getattr(block, "name", None)
            if tool_name:
                inp = getattr(block, "input", None)
                tool_uses.append((tool_name, json.dumps(inp)[:120] if inp else ""))
            text = getattr(block, "text", None)
            if isinstance(text, str) and text.strip():
                last_assistant_text = text

    print(f"content blocks by type: {dict(block_counts)}")
    if tool_uses:
        print(f"tool uses ({len(tool_uses)}):")
        for name, inp in tool_uses:
            print(f"  - {name}: {inp}")

    result = next((m for m in messages if type(m).__name__ == "ResultMessage"), None)
    if result is not None:
        print(
            f"result: is_error={getattr(result, 'is_error', None)} "
            f"subtype={getattr(result, 'subtype', None)} "
            f"duration_ms={getattr(result, 'duration_ms', None)} "
            f"num_turns={getattr(result, 'num_turns', None)}"
        )

    if last_assistant_text:
        print(f"\nlast assistant text (truncated 400 chars):\n  {last_assistant_text[:400]!r}")


async def main() -> int:
    if not _CONFIG_DIR.exists():
        print(f"ERROR: CLAUDE_CONFIG_DIR ({_CONFIG_DIR}) does not exist.", file=sys.stderr)
        return 1
    if not SKILL_DIR.exists():
        print(f"ERROR: skill dir not found: {SKILL_DIR}", file=sys.stderr)
        return 1

    run_cwd = SKILL_DIR.resolve()
    run_id = uuid.uuid4().hex[:8]
    output_filename = f"output_sdk_{run_id}.json"
    prompt = build_prompt(run_cwd, output_filename)
    expected_session_dir = session_dir_for_cwd(run_cwd, _CONFIG_DIR)
    print(f"running SDK in:       {run_cwd}")
    print(f"output filename:      {output_filename}  (per-run, won't clobber siblings)")
    print(f"expected session dir: {expected_session_dir}")
    print(f"prompt: {prompt}\n")

    messages, started_at = await run_sdk(run_cwd, prompt)
    summarize_sdk_stream(messages)

    jsonl = newest_jsonl_in(expected_session_dir, started_at)
    if jsonl is None:
        print(f"\nWARN: no new jsonl in {expected_session_dir} since start; "
              f"searching all of projects/...")
        jsonl = newest_jsonl_in(_CONFIG_DIR / "projects", started_at)

    if jsonl is None:
        print("\n=== JSONL trace ===")
        print("NO new session log was written by the SDK run.")
        print("→ The SDK path does NOT give you the same JSONL the CLI produces.")
        print("→ If the optimizer ingests JSONL from disk, either keep the CLI path")
        print("  for trace generation, or capture the SDK message stream and write")
        print("  your own trace format.")
    else:
        analyze_trace(jsonl)
        print("\n=== verdict ===")
        print("SDK run DID produce a JSONL trace at the expected location.")
        print("→ You can drop the CLI subprocess path and still feed the optimizer.")

    output_json = run_cwd / output_filename
    if output_json.exists():
        print(f"\n=== {output_filename} produced by SDK run ===")
        print(f"path: {output_json}")
        print(output_json.read_text())
        print(f"\n(per-run output left in skill dir — clean up with: "
              f"rm {run_cwd}/output_sdk_*.json)")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
