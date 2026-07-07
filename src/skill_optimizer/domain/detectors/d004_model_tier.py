"""D004 ModelTier — detect when all traces use a large-tier model (downgrade candidate).

Owns the model-tier policy data (`_DOWNGRADE_PATH`, `_LARGE_MODELS`, `_TARGET_MODEL`)
since detection is the upstream concept. The paired `model_swap` mutation imports
these constants from here.
"""
from __future__ import annotations

from datetime import UTC, datetime

from skill_optimizer.config.calibration import CALIBRATION
from skill_optimizer.config.pricing import Pricing
from skill_optimizer.domain.token_usage import cost_at_model, trace_cost
from skill_optimizer.domain.types import Finding
from skill_optimizer.ports.trace_store import CapturedRun

_DOWNGRADE_PATH = {
    "claude-opus-4-7":   "claude-sonnet-4-6",
    "claude-opus-4":     "claude-sonnet-4",
    "claude-sonnet-4-6": "claude-haiku-4-5",
    "claude-sonnet-4":   "claude-haiku-4-5",
}
_LARGE_MODELS = frozenset(_DOWNGRADE_PATH)
_TARGET_MODEL = "claude-haiku-4-5"  # fallback when SKILL.md declares no model


def detect_model_tier_overkill(
    skill_id: str,
    runs: list[CapturedRun],
    pricing: Pricing,
    min_occurrences: int = CALIBRATION.d004_min_occurrences,
) -> list[Finding]:
    """D004: emit a Finding when every captured trace uses a large-tier model.

    Skeleton heuristic (deterministic). Real D004 is LLM-judged — given input/
    output excerpts, ask Haiku 'could this turn have been done by Haiku?'
    """
    if not runs:
        return []

    all_models: set[str] = set()
    large_only_runs = 0
    evidence: list[dict] = []

    for run in runs:
        models = run.trace.models_used
        for m in models:
            all_models.add(m)
        if models and all(m in _LARGE_MODELS for m in models):
            large_only_runs += 1
            if len(evidence) < 5:
                evidence.append({
                    "trace_ref": f"run_{run.run_id:03d}.jsonl",
                    "fragment": (
                        f"models={list(models)}; "
                        f"output_keys={sorted(run.output.keys())[:5]}"
                    ),
                })

    if large_only_runs < min_occurrences:
        return []
    if not all_models or not all(m in _LARGE_MODELS for m in all_models):
        return []

    cost_pct = _estimate_model_swap_savings(runs, _TARGET_MODEL, pricing)

    ts = datetime.now(UTC).strftime("%Y-%m-%d")
    return [Finding(
        finding_id=f"skopt-{ts}-d004-{skill_id}-001",
        detector_id="D004",
        skill_id=skill_id,
        category="model_tier_overkill",
        observed_pattern=(
            f"All {large_only_runs} traces use large-tier models "
            f"({sorted(all_models)}) for {skill_id}'s bounded structured-output task. "
            f"Recommendation: downgrade one tier cheaper."
        ),
        evidence=tuple(evidence),
        estimated_cost_pct=cost_pct,
        estimated_latency_pct=CALIBRATION.d004_tier_latency_pct,
        quality_risk="low",
        occurrences=large_only_runs,
    )]


def _estimate_model_swap_savings(
    runs: list[CapturedRun],
    target_model: str,
    pricing: Pricing,
) -> float:
    """Reprice each baseline trace at ``target_model`` and average the % delta.

    Per-trace ratio (not aggregate-then-divide) so traces with different
    cache-vs-direct-input mixes contribute proportionally.
    """
    pcts: list[float] = []
    for run in runs:
        baseline = trace_cost(run.trace, pricing)
        if baseline <= 0:
            continue
        try:
            target = cost_at_model(run.trace, pricing, target_model)
        except KeyError:
            continue
        pcts.append((target - baseline) / baseline * 100.0)
    return sum(pcts) / len(pcts) if pcts else 0.0
