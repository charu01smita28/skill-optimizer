"""Ground-truth check for a D012-extracted ``helper.py``.

The optimizer's verifier proves baseline-equivalence (patched output matches the
captured baseline output on the skill's declared primary fields). This script is
the *complementary* check: it runs the extracted helper directly against
hand-authored expected outputs (``demo/skills/<skill>/expected_outputs/``) and
reports any divergence on the FULL output object — every field, not just the
ones the verifier checks. Stronger gate, narrower scope (only skills with
labeled expected outputs).

Usage (from repo root):
    python scripts/d012_helper_groundtruth.py                    # latest run, default skill
    python scripts/d012_helper_groundtruth.py --run runs/2026-05-13T09-08-57
    python scripts/d012_helper_groundtruth.py --skill invoice_validator
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SKILL = "invoice_validator"


def _latest_run(runs_root: Path) -> Path | None:
    if not runs_root.exists():
        return None
    candidates = sorted((p for p in runs_root.iterdir() if p.is_dir()), reverse=True)
    return candidates[0] if candidates else None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--skill", default=DEFAULT_SKILL,
                   help=f"Skill name (default: {DEFAULT_SKILL}).")
    p.add_argument("--run", type=Path, default=None,
                   help="Run directory under runs/ (default: latest).")
    p.add_argument("--runs-root", type=Path, default=REPO_ROOT / "runs")
    args = p.parse_args()

    run_dir = args.run or _latest_run(args.runs_root)
    if run_dir is None or not run_dir.exists():
        print(f"ERROR: no run dir found under {args.runs_root}", file=sys.stderr)
        return 1

    optimized_skill_dir = run_dir / "optimized" / args.skill
    helper = optimized_skill_dir / "helper.py"
    if not helper.exists():
        print(f"ERROR: helper.py not found at {helper}", file=sys.stderr)
        print("       (was D012 AUTO_APPLY in this run?)", file=sys.stderr)
        return 1

    expected_dir = REPO_ROOT / "demo" / "skills" / args.skill / "expected_outputs"
    if not expected_dir.exists():
        print(f"ERROR: no expected_outputs/ for {args.skill} at {expected_dir}", file=sys.stderr)
        return 1

    sample_inputs = optimized_skill_dir / "sample_inputs"
    inputs = sorted(sample_inputs.glob("*.json"))
    if not inputs:
        print(f"ERROR: no inputs in {sample_inputs}", file=sys.stderr)
        return 1

    print(f"run    : {run_dir}")
    print(f"helper : {helper}")
    print(f"expected: {expected_dir}")
    print(f"inputs : {len(inputs)}")
    print()

    failures: list[tuple[str, str]] = []
    for inp in inputs:
        expected_path = expected_dir / inp.name
        if not expected_path.exists():
            print(f"  {inp.name}: SKIP (no expected_outputs/{inp.name})")
            continue
        expected = json.loads(expected_path.read_text())

        # Run helper.py with cwd=optimized_skill_dir so the relative `output.json`
        # write lands where we can read it. Pass the input as the helper's argv[1]
        # exactly the way the rewritten SKILL.md tells the model to.
        result = subprocess.run(
            [sys.executable, "helper.py", f"sample_inputs/{inp.name}"],
            cwd=optimized_skill_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print(f"  {inp.name}: FAIL (helper.py exited {result.returncode})")
            print(f"           stderr: {result.stderr.strip()[:200]}")
            failures.append((inp.name, "non-zero exit"))
            continue

        actual_path = optimized_skill_dir / "output.json"
        if not actual_path.exists():
            print(f"  {inp.name}: FAIL (no output.json written)")
            failures.append((inp.name, "no output.json"))
            continue
        actual = json.loads(actual_path.read_text())

        if actual == expected:
            print(f"  {inp.name}: PASS  valid={actual['valid']}")
        else:
            diff_summary = _summarize_diff(expected, actual)
            print(f"  {inp.name}: FAIL  {diff_summary}")
            failures.append((inp.name, diff_summary))

    print()
    if failures:
        print(f"FAIL: {len(failures)}/{len(inputs)} inputs disagree with expected_outputs/")
        for name, why in failures:
            print(f"  - {name}: {why}")
        return 1
    print(f"PASS: helper.py matches expected_outputs/ on all {len(inputs)} inputs")
    return 0


def _summarize_diff(expected: dict, actual: dict) -> str:
    """One-line summary of which top-level keys disagree."""
    bad = [k for k in set(expected) | set(actual) if expected.get(k) != actual.get(k)]
    if not bad:
        return "(equal — investigate)"
    bits = []
    for k in bad:
        if k == "valid":
            bits.append(f"valid: expected={expected.get(k)!r} got={actual.get(k)!r}")
        elif k == "computed":
            ec, ac = expected.get(k, {}), actual.get(k, {})
            field_diffs = [
                f"{f} {ec.get(f)}→{ac.get(f)}"
                for f in set(ec) | set(ac) if ec.get(f) != ac.get(f)
            ]
            bits.append(f"computed differs: {', '.join(field_diffs)}")
        elif k == "discrepancies":
            n_exp = len(expected.get(k, []))
            n_got = len(actual.get(k, []))
            bits.append(f"discrepancies count: expected={n_exp} got={n_got}")
        else:
            bits.append(f"{k}: differs")
    return "; ".join(bits)


if __name__ == "__main__":
    raise SystemExit(main())
