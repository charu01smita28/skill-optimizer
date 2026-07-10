"""Dump tool-use shape per assistant turn for the most recent skopt-verify trace.

Used to answer "did the model actually parallelize?" after a D008 verifier run.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _newest_skopt_jsonl() -> Path | None:
    cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".aklaude")
    projects = cfg / "projects"
    if not projects.exists():
        return None
    jsonls = [
        p for p in projects.rglob("*.jsonl")
        if "skopt-verify" in str(p)
    ]
    if not jsonls:
        return None
    return max(jsonls, key=lambda p: p.stat().st_mtime)


def main() -> int:
    p = _newest_skopt_jsonl()
    if p is None:
        print("no skopt-verify trace found under CLAUDE_CONFIG_DIR/projects")
        return 1
    print(f"trace: {p}\n")

    turns: list[list[tuple[str, str]]] = []
    for line in p.open():
        r = json.loads(line)
        if r.get("type") != "assistant":
            continue
        content = r.get("message", {}).get("content", []) or []
        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if not tool_uses:
            continue
        per_turn: list[tuple[str, str]] = []
        for b in tool_uses:
            inp = b.get("input") or {}
            label = (inp.get("file_path") or inp.get("pattern") or inp.get("command") or "")
            per_turn.append((b["name"], str(label)[-50:]))
        turns.append(per_turn)

    print(f"assistant turns with tool_use: {len(turns)}")
    parallel_count = 0
    for i, turn in enumerate(turns, start=1):
        n = len(turn)
        kind = "PARALLEL" if n > 1 else "sequential"
        if n > 1:
            parallel_count += 1
        print(f"  Turn {i} ({n} tool_use, {kind}):")
        for tool, label in turn:
            print(f"      {tool}: {label}")

    print(f"\nparallel turns: {parallel_count} / {len(turns)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
