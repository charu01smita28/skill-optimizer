"""Optimization report writer + composed-skill builder + 4-quadrant aggregator."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from skill_optimizer.domain.types import (
    DecisionVerdict,
    OptimizationDecision,
    OptimizationReport,
)


def write_report(report: OptimizationReport, run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    out_path = run_dir / "optimization_report.json"
    out_path.write_text(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
    return out_path


def compose_optimized_skill(
    staged_skill_dir: Path,
    decisions: list[OptimizationDecision],
    run_dir: Path,
    original_skill_dir: Path,
) -> Path | None:
    """Publish the staged skill to ``run_dir/optimized/`` + manifest. Prunes replay side-effects
    (legit fileset = original files ∪ each AUTO_APPLY patch's new_files). None if no AUTO_APPLY."""
    applied = [d for d in decisions if d.decision == DecisionVerdict.AUTO_APPLY]
    if not applied:
        return None

    optimized_dir = (run_dir / "optimized" / staged_skill_dir.name).resolve()
    if optimized_dir.exists():
        shutil.rmtree(optimized_dir)
    optimized_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staged_skill_dir, optimized_dir)

    keep: set[str] = {
        str(p.relative_to(original_skill_dir))
        for p in original_skill_dir.rglob("*") if p.is_file()
    }
    for d in applied:
        keep.update(d.proposal.patch.new_files.keys())
    _prune_to(optimized_dir, keep)

    manifest = [{
        "decision_id": d.decision_id,
        "mutation_type": d.proposal.mutation_type,
        "tier": d.proposal.tier,
        "target": d.proposal.patch.target_relative_path,
        "description": d.proposal.patch.description,
    } for d in applied]
    manifest_path = optimized_dir.parent / "applied_patches.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return optimized_dir


def _prune_to(root: Path, keep_relative: set[str]) -> None:
    """Delete files under ``root`` not in ``keep_relative``; remove now-empty dirs."""
    for p in list(root.rglob("*")):
        if p.is_file() and str(p.relative_to(root)) not in keep_relative:
            p.unlink()
    for d in sorted(
        (p for p in root.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        if not any(d.iterdir()):
            d.rmdir()


def stage_accepted_patches(
    skill_dir: Path,
    decisions: list[OptimizationDecision],
    dest_dir: Path,
) -> Path:
    """Copy ``skill_dir`` to ``dest_dir``, applying every AUTO_APPLY patch in decision order.
    Mirrors the dispatch loop's incremental scratch dir; here so composition is unit-testable.
    """
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_dir, dest_dir)
    for d in decisions:
        if d.decision == DecisionVerdict.AUTO_APPLY:
            d.proposal.patch.apply_to(dest_dir)
    return dest_dir


def build_quadrants(decisions: list[OptimizationDecision]) -> list[dict]:
    """Aggregate the four dimensions across decisions for the report."""
    if not decisions:
        return []
    chosen = next((d for d in decisions if d.decision == DecisionVerdict.AUTO_APPLY), decisions[0])
    v = chosen.verification
    return [
        {"name": "cost", "baseline": None, "patched": None,
         "delta_pct": v.cost_delta_pct,
         "notes": "measured: sum of patched-replay tokens × per-model rates from pricing.yaml"},
        {"name": "latency", "baseline": None, "patched": None,
         "delta_pct": v.latency_delta_pct,
         "notes": "measured: avg(patched elapsed_s) vs baseline elapsed_s"},
        {"name": "quality", "baseline": 1.0, "patched": v.equivalence_ratio,
         "delta_pct": v.quality_delta * 100.0,
         "notes": f"strict-eq on primary fields ({len(v.comparisons)} compared)"},
        {"name": "reliability", "baseline": None, "patched": None,
         "delta_pct": 0.0, "notes": "spike: not measured"},
    ]
