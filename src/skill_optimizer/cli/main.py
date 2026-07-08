"""skopt CLI.

Usage:
    skopt optimize \\
        --skill demo/skills/ticket_router \\
        --traces-root traces --holdout 2

Outputs in ``runs/<ISO timestamp>/``: ``decisions.jsonl``, ``optimization_report.json``.
"""
from __future__ import annotations

import argparse
import random
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from skill_optimizer.adapters.claude_cli_adapter import ClaudeCliAdapter
from skill_optimizer.adapters.claude_cli_eval_adapter import ClaudeCliEvalAdapter
from skill_optimizer.adapters.jsonl_trace_store import JsonlTraceStore
from skill_optimizer.config.pricing import load_pricing
from skill_optimizer.domain.decision_policy import append_decision, decide
from skill_optimizer.domain.detectors import (
    detect_deterministic_steps,
    detect_env_setup_repeat,
    detect_model_tier_overkill,
    detect_pseudoparallelizable_tool_calls,
    detect_redundant_lookups,
    detect_script_rederivation,
    detect_tool_reliability_failures,
    detect_verbose_prompt,
)
from skill_optimizer.domain.mutations import (
    propose_cache_strategy_rewrite,
    propose_helper_extract,
    propose_model_swap,
    propose_preload_file,
    propose_prompt_rewrite,
    propose_pseudoparallelize_tools,
    propose_step_determinize,
    propose_tool_guidance_rewrite,
)
from skill_optimizer.domain.report import (
    build_quadrants,
    compose_optimized_skill,
    write_report,
)
from skill_optimizer.domain.types import DecisionVerdict, OptimizationReport
from skill_optimizer.domain.verifier import verify
from skill_optimizer.ports.llm_client import LLMClient, LLMClientError
from skill_optimizer.ports.trace_store import CapturedRun
from skill_optimizer.preflight import preflight_or_exit
from skill_optimizer.runtime import setup_aklaude_env
from skill_optimizer.skill_md import read_output_path

SPLIT_SEED = 42


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="skopt", description="Skill optimizer (spike).")
    sub = p.add_subparsers(dest="cmd", required=True)
    opt = sub.add_parser("optimize", help="Run the full optimization pipeline.")
    opt.add_argument("--skill", required=True, type=Path)
    opt.add_argument("--traces-root", required=True, type=Path)
    opt.add_argument("--traces-subdir", default="baseline",
                     help="Subdirectory under <traces-root>/<skill_name>/ containing the JSONL "
                          "traces (default: 'baseline'). Set this if your traces live in a "
                          "differently-named folder, e.g. --traces-subdir production_runs.")
    opt.add_argument("--inputs-dir", type=Path, default=None,
                     help="Directory containing the skill's input files (default: "
                          "<skill>/sample_inputs/). Set this if your inputs live elsewhere.")
    opt.add_argument("--holdout", type=int, default=2,
                     help="Number of distinct holdout inputs to verify against (default: 2).")
    opt.add_argument("--runs-root", type=Path, default=Path("runs"))
    opt.add_argument("--config-dir", type=Path, default=Path("~/.aklaude").expanduser(),
                     help="CLAUDE_CONFIG_DIR for the patched-skill replay.")
    return p.parse_args()


def _split_runs(
    runs: list[CapturedRun], holdout: int, seed: int,
) -> tuple[list[CapturedRun], list[CapturedRun]]:
    """Pick ``holdout`` distinct inputs; replays of one input stay together."""
    rng = random.Random(seed)
    by_input: dict[str, list[CapturedRun]] = {}
    for r in runs:
        by_input.setdefault(r.input_filename, []).append(r)
    inputs = list(by_input.keys())
    rng.shuffle(inputs)
    # Leave ≥1 input for training (no train/holdout overlap).
    max_holdout = max(1, len(inputs) - 1)
    n_holdout = min(holdout, max_holdout)
    if n_holdout < holdout:
        print(
            f"  (clamping --holdout {holdout} → {n_holdout}: "
            f"only {len(inputs)} unique inputs, must leave ≥1 for training)"
        )
    return (
        [r for inp in inputs[n_holdout:] for r in by_input[inp]],   # train
        [r for inp in inputs[:n_holdout] for r in by_input[inp]],   # holdout
    )


def _make_run_id() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")


def _resolve_baseline_dir(traces_root: Path, skill_id: str, traces_subdir: str) -> Path | None:
    """Find the directory holding *.jsonl traces.

    Tries (in order):
      1. ``<traces_root>/*.jsonl`` directly — flat layout, easiest BYO drop-in.
      2. ``<traces_root>/<skill_id>/<traces_subdir>/`` — structured layout
         that ``capture_traces.py`` writes (multi-skill corpora).

    Returns the resolved directory, or ``None`` if neither contains JSONLs.
    """
    flat = traces_root.resolve()
    if flat.is_dir() and any(flat.glob("*.jsonl")):
        return flat
    structured = (traces_root / skill_id / traces_subdir).resolve()
    if structured.is_dir() and any(structured.glob("*.jsonl")):
        return structured
    return None


def _print_cost_summary(decisions: list) -> None:
    """Per-mutation cost: marginal (vs the prior accepted state) + cumulative (vs baseline).
    Marginal is the diff of consecutive cumulatives; only AUTO_APPLY advances the state."""
    if not decisions:
        return
    print("\nCost (marginal = vs prior accepted state · cumulative = vs baseline):")
    prev = 0.0
    for d in decisions:
        cum = d.verification.cost_delta_pct
        det = d.proposal.finding.detector_id
        mt = d.proposal.mutation_type
        print(f"  {det:<5} {mt:<22} marginal {cum - prev:+7.1f}pp   cumulative {cum:+7.1f}%   "
              f"{d.decision.value}")
        if d.decision == DecisionVerdict.AUTO_APPLY:
            prev = cum


def cmd_optimize(args: argparse.Namespace) -> int:
    skill_dir: Path = args.skill.resolve()
    if not (skill_dir / "SKILL.md").exists():
        print(f"ERROR: SKILL.md not found in {skill_dir}", file=sys.stderr)
        return 1
    skill_id = skill_dir.name
    sample_inputs_dir = (args.inputs_dir or (skill_dir / "sample_inputs")).resolve()
    if not sample_inputs_dir.exists():
        print(f"ERROR: inputs dir not found at {sample_inputs_dir} "
              f"(use --inputs-dir to point at a different location)", file=sys.stderr)
        return 1
    baseline_dir = _resolve_baseline_dir(args.traces_root, skill_id, args.traces_subdir)
    if baseline_dir is None:
        flat = args.traces_root.resolve()
        structured = (args.traces_root / skill_id / args.traces_subdir).resolve()
        print(
            f"ERROR: no *.jsonl traces found at:\n"
            f"  {flat}/*.jsonl                              (flat layout)\n"
            f"  {structured}/*.jsonl    (structured layout)\n"
            f"Drop your JSONL files at either location, or override --traces-subdir.",
            file=sys.stderr,
        )
        return 1
    if not args.config_dir.exists():
        print(f"ERROR: CLAUDE_CONFIG_DIR not found: {args.config_dir}", file=sys.stderr)
        return 1

    setup_aklaude_env(args.config_dir)
    preflight_or_exit()  # fail fast on auth expiry

    llm_client: LLMClient | None
    try:
        llm_client = ClaudeCliAdapter(timeout_s=120)
    except LLMClientError as exc:
        print(f"  (LLMClient unavailable, Tier-2 mutations disabled: {exc})")
        llm_client = None

    output_filename = Path(read_output_path(skill_dir)).name
    runs = JsonlTraceStore(
        baseline_dir, sample_inputs_dir, output_filename=output_filename,
    ).list_runs()
    print(f"Loading runs from {baseline_dir} ... {len(runs)} runs")
    if not runs:
        return 1

    train_runs, holdout_runs = _split_runs(runs, args.holdout, SPLIT_SEED)
    n_unique_holdout = len({r.input_filename for r in holdout_runs})
    print(
        f"Splitting (seed={SPLIT_SEED}): {len(train_runs)} train, "
        f"{n_unique_holdout} unique holdout input(s) (--holdout={args.holdout})"
    )

    pricing = load_pricing()
    skill_md_text = (skill_dir / "SKILL.md").read_text()
    findings: list = []
    findings += detect_model_tier_overkill(skill_id=skill_id, runs=train_runs, pricing=pricing)
    findings += detect_redundant_lookups(skill_id=skill_id, runs=train_runs, pricing=pricing)
    findings += detect_pseudoparallelizable_tool_calls(skill_id=skill_id, runs=train_runs, pricing=pricing)
    findings += detect_tool_reliability_failures(skill_id=skill_id, runs=train_runs, pricing=pricing)
    findings += detect_env_setup_repeat(skill_id=skill_id, runs=train_runs, pricing=pricing)
    findings += detect_deterministic_steps(
        skill_id=skill_id, skill_dir=skill_dir, runs=train_runs, pricing=pricing,
    )
    findings += detect_verbose_prompt(skill_id=skill_id, skill_md_text=skill_md_text, runs=train_runs, pricing=pricing)
    findings += detect_script_rederivation(skill_id=skill_id, runs=train_runs, pricing=pricing)
    print(f"Detection: {len(findings)} Finding(s)")
    for f in findings:
        print(f"  - {f.detector_id} {f.category} (occurrences={f.occurrences})")
    if not findings:
        print("No findings — nothing to optimize.")
        return 0

    run_id = _make_run_id()
    run_dir = (args.runs_root / run_id).resolve()
    decisions_path = run_dir / "decisions.jsonl"
    decisions: list = []

    # Scratch dir = the cumulative accepted state: rewriters read SKILL.md from
    # here (so they see prior accepted changes), verify() patches on top, AUTO_APPLY advances it.
    scratch_root = Path(tempfile.mkdtemp(prefix="skopt-stage-"))
    scratch_skill_dir = scratch_root / skill_id
    shutil.copytree(skill_dir, scratch_skill_dir)
    tier1_mutations = {
        "D004": propose_model_swap,
        "D001": propose_preload_file,
    }
    tier2_mutations = {
        "D008": propose_pseudoparallelize_tools,
        "D003": propose_tool_guidance_rewrite,
        "D006": propose_cache_strategy_rewrite,
        "D005": propose_step_determinize,
        "D007": propose_prompt_rewrite,
        "D012": propose_helper_extract,
    }
    try:
        for finding in findings:
            current_skill_text = (scratch_skill_dir / "SKILL.md").read_text()
            det = finding.detector_id
            if det in tier1_mutations:
                proposal = tier1_mutations[det](finding, current_skill_text=current_skill_text)
            elif det in tier2_mutations:
                if llm_client is None:
                    print(f"  (skip {finding.finding_id}: {det} mutation needs LLMClient — disabled)")
                    continue
                print(f"  ({det}: invoking LLM rewriter for {finding.finding_id}...)")
                proposal = tier2_mutations[det](
                    finding, current_skill_text=current_skill_text, llm_client=llm_client,
                )
            else:
                print(f"  (skip {finding.finding_id}: no mutation registered for {det})")
                continue
            if proposal is None:
                print(f"  (skip {finding.finding_id}: mutation produced nothing applicable)")
                continue
            if proposal.patch.full_file:
                print(f"Proposal: {proposal.mutation_type} (full-file rewrite, "
                      f"{len(proposal.patch.after_text)} chars)")
            else:
                before_summary = proposal.patch.before_text or "(insert)"
                print(f"Proposal: {proposal.mutation_type} "
                      f"({before_summary} → {proposal.patch.after_text})")

            print(f"Verifier: replaying patched skill on {n_unique_holdout} unique input(s)...")
            verification = verify(
                proposal=proposal,
                skill_dir=scratch_skill_dir,
                holdout_runs=holdout_runs,
                pricing=pricing,
                eval_harness=ClaudeCliEvalAdapter(),
                inputs_dir=sample_inputs_dir,
            )
            print(
                f"Verification: equivalence={verification.equivalence_ratio:.2f}, "
                f"cost_delta={verification.cost_delta_pct:.1f}%, "
                f"latency_delta={verification.latency_delta_pct:.1f}%, "
                f"verdict={verification.verdict}"
            )

            decision = decide(proposal=proposal, verification=verification)
            print(f"Decision: {decision.decision.value}")
            append_decision(decision, decisions_path)
            decisions.append(decision)
            if decision.decision == DecisionVerdict.AUTO_APPLY:
                decision.proposal.patch.apply_to(scratch_skill_dir)

        report = OptimizationReport(
            run_id=run_id,
            skill_id=skill_id,
            started_at=run_id,
            decisions=tuple(decisions),
            quadrants=tuple(build_quadrants(decisions)),
        )
        report_path = write_report(report, run_dir)
        print(f"Wrote {decisions_path}")
        print(f"Wrote {report_path}")

        optimized_dir = compose_optimized_skill(
            scratch_skill_dir, decisions, run_dir, original_skill_dir=skill_dir,
        )
        if optimized_dir:
            n_applied = sum(1 for d in decisions if d.decision == DecisionVerdict.AUTO_APPLY)
            print(f"Composed {n_applied} patch(es) → {optimized_dir}")
        else:
            print("No AUTO_APPLY decisions — skipping composition.")
        _print_cost_summary(decisions)
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)
    return 0


def main() -> int:
    load_dotenv()
    args = _parse_args()
    return cmd_optimize(args) if args.cmd == "optimize" else 2


if __name__ == "__main__":
    raise SystemExit(main())
