"""Per-mutation cost breakdown for an optimize run.

    python scripts/show_run.py                 # newest runs/<ts>/
    python scripts/show_run.py runs/2026-...   # a specific run dir

Each decision's `cost_delta_pct` is cumulative (vs the baseline traces); the
marginal column is the change from the prior accepted state — "what this one
buys on top of the ones above". Only AUTO_APPLY advances the accepted state.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) > 1:
        run_dir = Path(sys.argv[1])
    else:
        runs = sorted((Path("runs").glob("*/") if Path("runs").exists() else []),
                      key=lambda p: p.name)
        if not runs:
            print("no runs/ found (run from repo root)", file=sys.stderr)
            return 1
        run_dir = runs[-1]

    decisions_path = run_dir / "decisions.jsonl"
    if not decisions_path.exists():
        print(f"no decisions.jsonl in {run_dir}", file=sys.stderr)
        return 1

    decisions = [json.loads(line) for line in decisions_path.read_text().splitlines() if line.strip()]
    print(f"{run_dir.name}  ({len(decisions)} decision(s))")
    print("marginal = vs prior accepted state · cumulative = vs baseline\n")
    print(f"  {'det':<5} {'mutation':<22} {'marginal':>10} {'cumulative':>11} {'equiv':>6}  verdict")
    print(f"  {'-'*5} {'-'*22} {'-'*10} {'-'*11} {'-'*6}  {'-'*10}")
    prev = 0.0
    for d in decisions:
        v = d["verification"]
        cum = v["cost_delta_pct"]
        det = d["proposal"]["finding"]["detector_id"]
        mt = d["proposal"]["mutation_type"]
        verdict = d["decision"]
        print(f"  {det:<5} {mt:<22} {cum - prev:>+9.1f}pp {cum:>+10.1f}% {v['equivalence_ratio']:>6.2f}  {verdict}")
        if verdict == "AUTO_APPLY":
            prev = cum
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
