"""Replay the patched skill against held-out inputs and compare outputs.

``verify()`` is the entry point. It accepts an EvalHarness (defaults to the
production ClaudeCli adapter) so tests can swap in a fake.
"""
from __future__ import annotations

import json
import os
import shutil
import statistics
import tempfile
import time
from pathlib import Path

from skill_optimizer.config.calibration import CALIBRATION
from skill_optimizer.config.pricing import Pricing
from skill_optimizer.domain.token_usage import trace_cost
from skill_optimizer.domain.trace import Trace, parse_trace_file
from skill_optimizer.domain.types import (
    Proposal,
    ReplayResult,
    VerificationResult,
)
from skill_optimizer.ports.eval_harness import EvalHarness
from skill_optimizer.ports.trace_store import CapturedRun
from skill_optimizer.skill_md import build_replay_prompt, read_output_path, read_primary_fields


"""

  The invoice_validator story end-to-end

  baseline answer (captured):  {"valid": false, ...}
  patched skill = rewritten SKILL.md ("run python helper.py …") + new helper.py
     |
     run it 3× on invoice_012  →  each writes output.json  →  {"valid": false}
     |
     compare "valid": false == false  → match, match, match
     |
     equivalence_ratio = 3/3 = 1.0   → verdict = PASS
     cost_delta = big drop (helper run ≪ re-deriving every time)
  → VerificationResult(equivalence_ratio=1.0, cost_delta_pct=−…, verdict="PASS") → handed to decide().

"""

def verify(
    proposal: Proposal,
    skill_dir: Path,
    holdout_runs: list[CapturedRun],
    pricing: Pricing,
    n_replays_per_input: int = CALIBRATION.verifier_n_replays,
    eval_harness: EvalHarness | None = None,
    inputs_dir: Path | None = None,
) -> VerificationResult:
    """Apply patch in tempdir, replay each holdout input ``n_replays_per_input`` times,
    compare. N>1 produces a variance band so ``decide()`` can FLAG noisy wins.

    ``inputs_dir`` defaults to ``<skill_dir>/sample_inputs/``; pass an explicit
    path to read inputs from elsewhere. The skill's output filename comes from
    ``read_output_path(skill_dir)`` (frontmatter ``output_path:`` or default).
    """
    harness = eval_harness or _default_harness()
    inputs_dir = inputs_dir or (skill_dir / "sample_inputs")
    output_path = read_output_path(skill_dir)

    # Dedupe to one baseline per input — pick the FIRST captured replay's output/trace.
    baseline_by_input: dict[str, dict] = {}
    baseline_elapsed_by_input: dict[str, float] = {}
    baseline_trace_by_input: dict[str, Trace] = {}
    for r in holdout_runs:
        if r.input_filename not in baseline_by_input:
            baseline_by_input[r.input_filename] = r.output
            baseline_elapsed_by_input[r.input_filename] = r.elapsed_s
            baseline_trace_by_input[r.input_filename] = r.trace

    if not baseline_by_input:
        raise ValueError("holdout_runs is empty")

    primary_fields = _resolve_primary_fields(skill_dir, holdout_runs, baseline_by_input)

    replays: list[ReplayResult] = []
    with tempfile.TemporaryDirectory(prefix="skopt-verify-") as tmpdir:
        patched_skill_dir = (Path(tmpdir) / skill_dir.name).resolve()
        shutil.copytree(skill_dir, patched_skill_dir)
        proposal.patch.apply_to(patched_skill_dir)

        for input_name in baseline_by_input:
            for _ in range(n_replays_per_input):
                replays.append(_run_one_replay(
                    skill_dir=patched_skill_dir,
                    input_name=input_name,
                    inputs_dir=inputs_dir,
                    output_path=output_path,
                    harness=harness,
                ))

    comparisons: list[dict] = []
    patched_elapsed_by_replay: dict[str, list[float]] = {}
    for replay in replays:
        baseline = baseline_by_input.get(replay.input_filename, {})
        patched_elapsed_by_replay.setdefault(replay.input_filename, []).append(replay.elapsed_s)
        if replay.status != "ok":
            comparisons.append({
                "input": replay.input_filename,
                "baseline": baseline,
                "patched": replay.output,
                "matched": False,
                "diff": f"replay status={replay.status}",
            })
            continue
        matched, diff = _compare_primary_fields(baseline, replay.output, primary_fields)
        comparisons.append({
            "input": replay.input_filename,
            "baseline": baseline,
            "patched": replay.output,
            "matched": matched,
            "diff": diff,
        })

    equivalence_ratio = (
        sum(1 for c in comparisons if c["matched"]) / len(comparisons)
        if comparisons else 0.0
    )
    cost_delta_pct, cost_delta_stddev = measure_cost_delta(
        baseline_trace_by_input=baseline_trace_by_input,
        patched_replays=replays,
        pricing=pricing,
        fallback_pct=proposal.finding.estimated_cost_pct,
    )
    latency_delta_pct, latency_delta_stddev = _latency_delta_with_variance(
        baseline_elapsed_by_input, patched_elapsed_by_replay
    )
    quality_delta = equivalence_ratio - 1.0
    verdict = "PASS" if equivalence_ratio == 1.0 else "FAIL"

    return VerificationResult(
        proposal_id=proposal.proposal_id,
        holdout_inputs=len(baseline_by_input),
        comparisons=tuple(comparisons),
        equivalence_ratio=equivalence_ratio,
        cost_delta_pct=cost_delta_pct,
        latency_delta_pct=latency_delta_pct,
        quality_delta=quality_delta,
        reliability_delta=0.0,
        verdict=verdict,
        cost_delta_stddev=cost_delta_stddev,
        latency_delta_stddev=latency_delta_stddev,
        n_replays_per_input=n_replays_per_input,
    )


def _default_harness() -> EvalHarness:
    from skill_optimizer.adapters.claude_cli_eval_adapter import ClaudeCliEvalAdapter
    return ClaudeCliEvalAdapter()


def _run_one_replay(
    skill_dir: Path,
    input_name: str,
    inputs_dir: Path,
    output_path: str,
    harness: EvalHarness,
) -> ReplayResult:
    stale_output = skill_dir / output_path
    if stale_output.exists():
        stale_output.unlink()

    prompt = build_replay_prompt(
        skill_dir, input_name, inputs_dir=inputs_dir, output_path=output_path,
    )
    started_at = time.time()
    sdk_result = harness.run(
        skill_dir=skill_dir,
        prompt=prompt,
        allowed_tools=("Read", "Edit", "Write", "Bash"),
        timeout_s=CALIBRATION.verifier_replay_timeout_s,
    )
    trace_path = _locate_replay_trace(skill_dir, started_at)

    if sdk_result.status == "timeout":
        return ReplayResult(input_name, {}, sdk_result.elapsed_s, "timeout", trace_path)
    if sdk_result.status == "failed":
        return ReplayResult(
            input_name,
            {"_error": sdk_result.error or "unknown SDK failure"},
            sdk_result.elapsed_s,
            "failed",
            trace_path,
        )

    output_file = skill_dir / output_path
    if not output_file.exists():
        return ReplayResult(
            input_name, {"_note": f"no {output_path} produced"}, sdk_result.elapsed_s, "failed",
            trace_path,
        )
    try:
        return ReplayResult(
            input_name,
            json.loads(output_file.read_text()),
            sdk_result.elapsed_s,
            "ok",
            trace_path,
        )
    except json.JSONDecodeError as e:
        return ReplayResult(
            input_name, {"_parse_error": str(e)}, sdk_result.elapsed_s, "failed",
            trace_path,
        )


def _locate_replay_trace(skill_dir: Path, since_epoch: float) -> str | None:
    """Find the JSONL the SDK just wrote under ``${CLAUDE_CONFIG_DIR}/projects/``."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if not config_dir:
        return None
    config_root = Path(config_dir) / "projects"
    if not config_root.exists():
        return None
    sanitized = str(skill_dir.resolve()).replace("/", "-").replace("_", "-")
    expected = config_root / sanitized
    found = _newest_jsonl(expected, since_epoch) or _newest_jsonl(config_root, since_epoch)
    return str(found) if found else None


def _newest_jsonl(dir_path: Path, since_epoch: float) -> Path | None:
    if not dir_path.exists():
        return None
    candidates = [p for p in dir_path.rglob("*.jsonl") if p.stat().st_mtime > since_epoch]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def measure_cost_delta(
    baseline_trace_by_input: dict[str, Trace],
    patched_replays: list[ReplayResult],
    pricing: Pricing,
    fallback_pct: float,
) -> tuple[float, float]:
    """Per-replay cost delta vs baseline; returns ``(mean_pct, stddev_pct)``.

    Falls back to ``(fallback_pct, 0.0)`` when no replay produced a usable trace.
    """
    pcts: list[float] = []
    for replay in patched_replays:
        if replay.status != "ok" or not replay.trace_path:
            continue
        baseline_trace = baseline_trace_by_input.get(replay.input_filename)
        if baseline_trace is None:
            continue
        try:
            patched_trace = parse_trace_file(Path(replay.trace_path))
        except (FileNotFoundError, ValueError):
            continue
        baseline_cost = trace_cost(baseline_trace, pricing)
        patched_cost = trace_cost(patched_trace, pricing)
        if baseline_cost <= 0:
            continue
        pcts.append((patched_cost - baseline_cost) / baseline_cost * 100.0)
    return _mean_stddev(pcts, fallback=fallback_pct)


def _mean_stddev(values: list[float], fallback: float) -> tuple[float, float]:
    if not values:
        return fallback, 0.0
    if len(values) < 2:
        return values[0], 0.0
    return statistics.mean(values), statistics.stdev(values)


def _compare_primary_fields(
    baseline: dict, patched: object, primary_fields: tuple[str, ...]
) -> tuple[bool, str]:
    if not primary_fields:
        return True, "no primary fields configured"
    if not isinstance(patched, dict):
        return False, f"patched output is {type(patched).__name__}, not dict — shape regression"
    diffs = [
        f"{f}: baseline={baseline.get(f)!r} patched={patched.get(f)!r}"
        for f in primary_fields if baseline.get(f) != patched.get(f)
    ]
    if diffs:
        return False, "; ".join(diffs)
    return True, f"all {len(primary_fields)} primary fields match"


def _latency_delta_with_variance(
    baseline_by_input: dict[str, float],
    patched_elapsed_by_replay: dict[str, list[float]],
) -> tuple[float, float]:
    """Per-replay latency delta vs the input's baseline; returns ``(mean_pct, stddev_pct)``."""
    deltas: list[float] = []
    for input_name, baseline_elapsed in baseline_by_input.items():
        if baseline_elapsed <= 0:
            continue
        for patched_elapsed in patched_elapsed_by_replay.get(input_name, []):
            deltas.append((patched_elapsed - baseline_elapsed) / baseline_elapsed * 100.0)
    return _mean_stddev(deltas, fallback=0.0)


def _resolve_primary_fields(
    skill_dir: Path,
    holdout_runs: list[CapturedRun],
    baseline_by_input: dict[str, dict],
) -> tuple[str, ...]:
    """Three-tier resolution for the verifier's equivalence-check field set:

    1. ``primary_fields:`` declared in SKILL.md frontmatter → author intent wins.
    2. Auto-derive from baseline: keys byte-stable across every replay of every
       input, intersected across inputs (needs ≥2 replays per input).
    3. Top-level keys of the first baseline output (all-keys fallback).
    """
    declared = read_primary_fields(skill_dir)
    if declared:
        return declared
    derived = _derive_primary_fields_from_baseline(holdout_runs)
    if derived:
        return derived
    first_output = next(iter(baseline_by_input.values()), {})
    return tuple(sorted(first_output.keys()))


def _derive_primary_fields_from_baseline(
    holdout_runs: list[CapturedRun],
) -> tuple[str, ...] | None:
    """Keys whose values are identical across all baseline replays of every input
    (intersection across inputs). None when fewer than 2 replays per input exist."""
    by_input: dict[str, list[dict]] = {}
    for r in holdout_runs:
        by_input.setdefault(r.input_filename, []).append(r.output)
    stable_per_input: list[set[str]] = []
    for outputs in by_input.values():
        if len(outputs) < 2:
            continue
        ref = outputs[0] or {}
        ref_keys = set(ref.keys())
        stable = {
            k for k in ref_keys
            if all(_json_eq(o.get(k), ref.get(k)) for o in outputs[1:])
        }
        stable_per_input.append(stable)
    if not stable_per_input:
        return None
    common = set.intersection(*stable_per_input)
    return tuple(sorted(common)) if common else None


def _json_eq(a: object, b: object) -> bool:
    """Stable equality for nested JSON values (sorts dict keys before comparing)."""
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
