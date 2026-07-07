"""Domain dataclasses: Finding, Patch, Proposal, VerificationResult,
OptimizationDecision, OptimizationReport. ``Trace`` lives in ``domain/trace.py``,
``CapturedRun`` in ``ports/trace_store.py``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

# ---------- Finding ----------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    """Detector output: one recurring waste pattern in a skill's traces."""
    finding_id: str
    detector_id: str
    skill_id: str
    category: str
    observed_pattern: str
    evidence: tuple[dict, ...]            # list of {trace_ref, fragment} dicts
    estimated_cost_pct: float             # negative = savings
    estimated_latency_pct: float
    quality_risk: str                     # "low" | "medium" | "high"
    occurrences: int

    def to_json(self) -> dict:
        return asdict(self) | {"evidence": [dict(e) for e in self.evidence]}


# ---------- Patch ------------------------------------------------------------

@dataclass(frozen=True)
class Patch:
    """A file rewrite ± companion ``new_files``. Modes: ``full_file=True`` overwrites;
    else ``before_text`` non-empty = substring replace, empty = insert into frontmatter."""
    target_relative_path: str
    before_text: str
    after_text: str
    description: str
    full_file: bool = False
    new_files: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)

    def apply_to(self, skill_dir: Path) -> None:
        for rel_path, content in self.new_files.items():
            new_file = skill_dir / rel_path
            new_file.parent.mkdir(parents=True, exist_ok=True)
            new_file.write_text(content)
        target = skill_dir / self.target_relative_path
        if not target.exists():
            raise FileNotFoundError(f"patch target missing: {target}")
        if self.full_file:
            target.write_text(self.after_text)
            return
        original = target.read_text()
        if not self.before_text:
            target.write_text(_insert_into_frontmatter(original, self.after_text))
            return
        if self.before_text not in original:
            raise ValueError(f"patch before_text not found in {target}")
        target.write_text(original.replace(self.before_text, self.after_text, 1))


def _insert_into_frontmatter(content: str, line: str) -> str:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return f"---\n{line}\n---\n\n{content}"
    for i, l in enumerate(lines[1:], start=1):
        if l.strip() == "---":
            new_lines = lines[:i] + [line] + lines[i:]
            return "\n".join(new_lines) + ("\n" if content.endswith("\n") else "")
    return f"---\n{line}\n---\n\n{content}"


# ---------- Proposal ---------------------------------------------------------

@dataclass(frozen=True)
class Proposal:
    """Finding + Patch. Estimated effect is carried on the Finding."""
    proposal_id: str
    finding: Finding
    patch: Patch
    tier: str                              # "1" (template) | "2" (LLM rewriter)
    mutation_type: str                     # "model_swap" | "preload_file" | ...

    def to_json(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "tier": self.tier,
            "mutation_type": self.mutation_type,
            "finding": self.finding.to_json(),
            "patch": self.patch.to_json(),
        }


# ---------- Verification -----------------------------------------------------

@dataclass(frozen=True)
class ReplayResult:
    input_filename: str
    output: dict
    elapsed_s: float
    status: str                            # "ok" | "failed" | "timeout"
    trace_path: str | None = None          # absolute path to the patched-replay JSONL


@dataclass(frozen=True)
class VerificationResult:
    proposal_id: str
    holdout_inputs: int
    comparisons: tuple[dict, ...]          # [{input, baseline, patched, matched, diff}]
    equivalence_ratio: float               # matched / total
    cost_delta_pct: float                  # mean across N replays per input
    latency_delta_pct: float               # mean across N replays per input
    quality_delta: float                   # equivalence_ratio - 1.0
    reliability_delta: float
    verdict: str                           # "PASS" | "FAIL"
    cost_delta_stddev: float = 0.0         # sample stddev of cost deltas across replays
    latency_delta_stddev: float = 0.0
    n_replays_per_input: int = 1

    def to_json(self) -> dict:
        return asdict(self) | {"comparisons": [dict(c) for c in self.comparisons]}


# ---------- Decision ---------------------------------------------------------

class DecisionVerdict(str, Enum):
    AUTO_APPLY = "AUTO_APPLY"
    FLAG = "FLAG"
    REJECT = "REJECT"


@dataclass(frozen=True)
class OptimizationDecision:
    decision_id: str
    proposal: Proposal
    verification: VerificationResult
    decision: DecisionVerdict
    human_rationale: str
    decided_at: str                        # ISO 8601 UTC

    @staticmethod
    def now_iso() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def to_json(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "decision": self.decision.value,
            "decided_at": self.decided_at,
            "human_rationale": self.human_rationale,
            "proposal": self.proposal.to_json(),
            "verification": self.verification.to_json(),
        }


# ---------- Report -----------------------------------------------------------

@dataclass(frozen=True)
class OptimizationReport:
    run_id: str
    skill_id: str
    started_at: str
    decisions: tuple[OptimizationDecision, ...]
    quadrants: tuple[dict, ...]            # [{name, baseline, patched, delta_pct, notes}]

    def to_json(self) -> dict:
        counts = {v.value: 0 for v in DecisionVerdict}
        for d in self.decisions:
            counts[d.decision.value] += 1
        # cost_delta_pct is cumulative; marginal = the change vs the prior AUTO_APPLY state.
        decision_dicts: list[dict] = []
        prev_accepted_cost = 0.0
        for d in self.decisions:
            j = d.to_json()
            cum = d.verification.cost_delta_pct
            j["marginal_cost_delta_pct"] = round(cum - prev_accepted_cost, 2)
            decision_dicts.append(j)
            if d.decision == DecisionVerdict.AUTO_APPLY:
                prev_accepted_cost = cum
        return {
            "run_id": self.run_id,
            "skill_id": self.skill_id,
            "started_at": self.started_at,
            "status_summary": counts,
            "quadrants": [dict(q) for q in self.quadrants],
            "decisions": decision_dicts,
        }
