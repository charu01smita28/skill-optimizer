"""Three-way AUTO_APPLY / FLAG / REJECT policy + JSONL audit-trail writer."""
from __future__ import annotations

import json
from pathlib import Path

from skill_optimizer.config.calibration import CALIBRATION
from skill_optimizer.domain.types import (
    DecisionVerdict,
    OptimizationDecision,
    Proposal,
    VerificationResult,
)


def decide(
    proposal: Proposal,
    verification: VerificationResult,
    min_cost_win_pct: float = CALIBRATION.min_cost_win_pct,
) -> OptimizationDecision:
    """Three-way variance-aware gate: AUTO_APPLY / FLAG / REJECT.

    Correctness is non-negotiable (equivalence must be perfect); the three-way
    split is about how *confident* the cost win is, judged on its variance band.

        VerificationResult
              |
              v
        equivalence perfect? ........ no ......>  REJECT   (output changed)
        (correctness_ok)
              | yes
              v
        worst case of cost band ..... yes .....>  AUTO_APPLY   (confident win)
        clears the gate?
        (cost_band_wins:
         mean + stddev <= -gate)
              | no
              v
        does the mean clear gate? ... yes .....>  FLAG   (real but noisy -> human review)
        (cost_mean_wins)
              | no
              v
            REJECT   (not enough saving)

    AUTO_APPLY: equivalence holds AND the high (pessimistic) side of the cost
        variance band (mean + 1·stddev) still clears -min_cost_win_pct.
    FLAG: equivalence holds, mean cost-delta passes the gate, but the band
        crosses the threshold (high side does not clear -min_cost_win_pct).
    REJECT: equivalence fails OR mean cost-delta does not pass the gate.
    """
    # correctness — all three reduce to "every replay matched the baseline"
    pass_ok = verification.verdict == "PASS"              # PASS only when equivalence == 1.0
    eq_ok = verification.equivalence_ratio >= 1.0         # equivalence is perfect
    quality_ok = verification.quality_delta >= 0.0        # quality_delta = equiv - 1.0, so also "perfect"

    # cost — judge the win on its variance band, not a single point
    cost_mean = verification.cost_delta_pct
    cost_stddev = verification.cost_delta_stddev
    cost_high_side = cost_mean + cost_stddev              # pessimistic end: savings are negative,
                                                          # so +stddev = the SMALLEST-saving case
    cost_mean_wins = cost_mean <= -min_cost_win_pct       # does the average beat the gate?
    cost_band_wins = cost_high_side <= -min_cost_win_pct  # does even the worst case beat the gate?

    correctness_ok = pass_ok and eq_ok and quality_ok

    if correctness_ok and cost_band_wins:
        verdict = DecisionVerdict.AUTO_APPLY
        rationale = (
            f"Holdout replay ({verification.holdout_inputs} inputs × "
            f"N={verification.n_replays_per_input}) shows "
            f"equivalence={verification.equivalence_ratio:.2f}, "
            f"cost_delta={cost_mean:.1f}% ± {cost_stddev:.1f}%, "
            f"latency_delta={verification.latency_delta_pct:.1f}% ± "
            f"{verification.latency_delta_stddev:.1f}%. "
            f"Mutation: {proposal.mutation_type} ({proposal.tier}). "
            f"Variance band stays on win side; all gates pass."
        )
    elif correctness_ok and cost_mean_wins:
        verdict = DecisionVerdict.FLAG
        rationale = (
            f"FLAG — mean cost_delta={cost_mean:.1f}% passes -{min_cost_win_pct}% gate "
            f"but high side of variance band ({cost_high_side:.1f}%) does not "
            f"(stddev={cost_stddev:.1f}% across {verification.n_replays_per_input} replays). "
            f"Mutation: {proposal.mutation_type} ({proposal.tier}). Human review needed."
        )
    else:
        verdict = DecisionVerdict.REJECT
        failed = []
        if not pass_ok:    failed.append(f"verdict={verification.verdict}")
        if not eq_ok:      failed.append(f"equivalence={verification.equivalence_ratio:.2f} < 1.0")
        if not cost_mean_wins: failed.append(f"cost_delta={cost_mean:.1f}% above -{min_cost_win_pct}%")
        if not quality_ok: failed.append(f"quality_delta={verification.quality_delta:.2f} (regression)")
        rationale = f"REJECT — {', '.join(failed)}. Mutation: {proposal.mutation_type} ({proposal.tier})."

    return OptimizationDecision(
        decision_id=f"{proposal.proposal_id}-decision",
        proposal=proposal,
        verification=verification,
        decision=verdict,
        human_rationale=rationale,
        decided_at=OptimizationDecision.now_iso(),
    )


def append_decision(decision: OptimizationDecision, jsonl_path: Path) -> None:
    """Append one JSON object per line."""
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(decision.to_json(), ensure_ascii=False))
        f.write("\n")
