"""Probe whether the parallel-tool-use system-prompt directive actually triggers
parallel tool_use emission. One-shot SDK invocation, counts parallel turns,
reports verdict. ~$0.10 of tokens per run.

Usage:
    python scripts/probe_parallelism.py
    python scripts/probe_parallelism.py --skill demo/skills/invoice_validator --input invoice_001.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from skill_optimizer.preflight import preflight_or_exit
from skill_optimizer.runtime import (
    PARALLEL_TOOL_USE_DIRECTIVE,
    run_skill,
    setup_aklaude_env,
)
from skill_optimizer.skill_md import build_replay_prompt


REPO_ROOT = Path(__file__).resolve().parent.parent


def newest_jsonl(dir_path: Path, since_epoch: float) -> Path | None:
    if not dir_path.exists():
        return None
    candidates = [p for p in dir_path.rglob("*.jsonl") if p.stat().st_mtime > since_epoch]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def count_parallel_turns(jsonl_path: Path) -> tuple[int, int]:
    """Return (parallel_turns, total_tool_use_turns) for the given trace."""
    total = 0
    parallel = 0
    for line in jsonl_path.open():
        r = json.loads(line)
        if r.get("type") != "assistant":
            continue
        content = r.get("message", {}).get("content", []) or []
        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if not tool_uses:
            continue
        total += 1
        if len(tool_uses) > 1:
            parallel += 1
    return parallel, total


def main() -> int:
    p = argparse.ArgumentParser(description="Probe parallel tool use with the SDK directive.")
    p.add_argument("--skill", type=Path, default=REPO_ROOT / "demo" / "skills" / "invoice_validator")
    p.add_argument("--input", default="invoice_001.json")
    args = p.parse_args()

    load_dotenv()
    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.aklaude")).expanduser()
    if not config_dir.exists():
        print(f"ERROR: CLAUDE_CONFIG_DIR ({config_dir}) does not exist.", file=sys.stderr)
        return 1
    setup_aklaude_env(config_dir)

    if not (args.skill / "SKILL.md").exists():
        print(f"ERROR: SKILL.md not found in {args.skill}", file=sys.stderr)
        return 1
    input_path = args.skill / "sample_inputs" / args.input
    if not input_path.exists():
        print(f"ERROR: input not found: {input_path}", file=sys.stderr)
        return 1

    preflight_or_exit()

    print("=" * 70)
    print("Parallel-tool-use directive (appended to Claude Code's system prompt):")
    print("=" * 70)
    print(PARALLEL_TOOL_USE_DIRECTIVE)
    print()
    print(f"Skill : {args.skill}")
    print(f"Input : {args.input}")
    print()
    print("Running one SDK invocation. Expected ~30-60s wall time.")
    print()

    with tempfile.TemporaryDirectory(prefix="probe-parallel-") as tmp_root:
        run_skill_dir = (Path(tmp_root) / args.skill.name).resolve()
        shutil.copytree(args.skill, run_skill_dir)
        prompt = build_replay_prompt(run_skill_dir, args.input)

        import time
        started_at = time.time()

        result = run_skill(
            skill_dir=run_skill_dir,
            prompt=prompt,
            allowed_tools=("Read", "Edit", "Write", "Bash"),
            timeout_s=240,
            max_turns=50,
        )

        print(f"SDK result: status={result.status}  elapsed={result.elapsed_s:.1f}s  "
              f"turns={result.num_turns}  is_error={result.is_error}")
        if result.error:
            print(f"  error: {result.error}")

        jsonl = newest_jsonl(config_dir / "projects", started_at)
        if jsonl is None:
            print("ERROR: no JSONL trace found", file=sys.stderr)
            return 1
        print(f"\nTrace : {jsonl}")

        parallel, total = count_parallel_turns(jsonl)
        print()
        print("=" * 70)
        print(f"VERDICT:  {parallel} / {total} tool-use turns were parallel")
        print("=" * 70)
        if parallel > 0:
            print(f"✓  Parallelism WORKS. {parallel} of {total} turns batched multiple tool calls.")
            print(f"   Pseudoparallelize rename is NOT needed — D008 can stay as parallelize_tools.")
        else:
            print(f"✗  Still zero parallel turns. Directive did not unlock parallelism.")
            print(f"   Pseudoparallelize rename is justified — runtime is genuinely blocked.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
