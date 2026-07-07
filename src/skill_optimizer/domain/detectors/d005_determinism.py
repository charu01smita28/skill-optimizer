"""D005: a field that is byte-identical across every replay of an input is
deterministic — flag it so ``step_determinize`` can move it to code. The
``full``/``full_primary``/``partial`` classification is carried in ``evidence[0]``.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from skill_optimizer.config.calibration import CALIBRATION
from skill_optimizer.config.pricing import Pricing
from skill_optimizer.domain.types import Finding
from skill_optimizer.ports.trace_store import CapturedRun
from skill_optimizer.skill_md import read_primary_fields

# Top-level keys the replay layer writes when a run failed — never real output.
_FAILED_RUN_KEYS = frozenset({"_error", "_note", "_parse_error"})

_INPUT_TEXT_CAP = 6000  # evidence carries enough input to seed the codegen + its tests


def detect_deterministic_steps(
    skill_id: str,
    runs: list[CapturedRun],
    pricing: Pricing | None = None,  # unused; kept for detector-call uniformity
    skill_dir: Path | None = None,
    min_inputs: int = CALIBRATION.d005_min_inputs,
    min_replays: int = CALIBRATION.d005_min_replays,
) -> list[Finding]:
    """Emit a Finding for output fields identical across every replay of every eligible input."""
    if not runs:
        return []

    primary_fields = read_primary_fields(skill_dir) if skill_dir else None

    by_input: dict[str, list[CapturedRun]] = {}
    for r in runs:
        out = r.output
        if not isinstance(out, dict) or not out or _FAILED_RUN_KEYS & set(out):
            continue  # drop failed/empty captures — they'd be trivially "identical"
        by_input.setdefault(r.input_filename, []).append(r)

    eligible = {inp: rs for inp, rs in by_input.items() if len(rs) >= min_replays}
    if len(eligible) < min_inputs:
        return []

    fields = _field_universe(primary_fields, eligible)
    if not fields:
        return []

    per_input: list[dict] = []
    stable_in_all: set[str] = set(fields)
    for inp, rs in sorted(eligible.items()):
        stable_here = {f for f in fields if len({_canon(r.output.get(f)) for r in rs}) == 1}
        stable_in_all &= stable_here
        rep = rs[0]
        per_input.append({
            "input_filename": inp,
            "trace_ref": f"run_{rep.run_id:03d}.jsonl",
            "n_replays": len(rs),
            "input_text": _cap(rep.input_text),
            "stable_fields": sorted(stable_here),
            "stable_values": {f: rep.output.get(f) for f in sorted(stable_here)},
            "representative_output": rep.output,
            "full_output_identical": len({_canon(r.output) for r in rs}) == 1,
        })

    if not stable_in_all:
        return []

    all_primary_stable = stable_in_all == set(fields)
    all_full_identical = all(p["full_output_identical"] for p in per_input)
    if all_primary_stable and all_full_identical:
        classification = "full"
    elif all_primary_stable:
        classification = "full_primary"
    else:
        classification = "partial"

    cost_pct, latency_pct = _estimate_savings(classification, len(stable_in_all), len(fields))
    per_input[0] = per_input[0] | {"classification": classification,
                                   "stable_fields_corpuswide": sorted(stable_in_all),
                                   "field_universe": list(fields)}

    ts = datetime.now(UTC).strftime("%Y-%m-%d")
    return [Finding(
        finding_id=f"skopt-{ts}-d005-{skill_id}-001",
        detector_id="D005",
        skill_id=skill_id,
        category="deterministic_steps",
        observed_pattern=_describe(classification, skill_id, sorted(stable_in_all), len(per_input)),
        evidence=tuple(per_input),
        estimated_cost_pct=cost_pct,
        estimated_latency_pct=latency_pct,
        quality_risk="medium",
        occurrences=len(per_input),
    )]


def _field_universe(
    primary_fields: tuple[str, ...] | None, eligible: dict[str, list[CapturedRun]],
) -> list[str]:
    """Declared primary_fields when present, else keys common to every eligible output."""
    if primary_fields:
        return list(primary_fields)
    common: set[str] | None = None
    for rs in eligible.values():
        for r in rs:
            keys = set(r.output)
            common = keys if common is None else (common & keys)
    return sorted(common or set())


def _estimate_savings(classification: str, n_stable: int, n_fields: int) -> tuple[float, float]:
    """Heuristic estimate; the verifier measures the real delta against replays."""
    if classification in ("full", "full_primary"):
        # Sub-mode C drops the LLM call entirely (or all but a thin prose pass).
        return (-100.0, -95.0) if classification == "full" else (-85.0, -80.0)
    frac = n_stable / n_fields if n_fields else 0.0
    return round(-60.0 * frac, 1), round(-50.0 * frac, 1)


def _describe(classification: str, skill_id: str, stable: list[str], n_inputs: int) -> str:
    stable_list = ", ".join(stable)
    if classification == "full":
        return (f"All {n_inputs} captured inputs produce byte-identical output across "
                f"replays — {skill_id} is fully deterministic and code-replaceable "
                f"(no LLM call needed).")
    if classification == "full_primary":
        return (f"All {n_inputs} captured inputs agree on every primary field "
                f"({stable_list}) across replays — the decision logic is deterministic; "
                f"free-text fields still vary.")
    return (f"Fields [{stable_list}] are identical across every replay of all {n_inputs} "
            f"captured inputs — deterministic and extractable to a helper; the remaining "
            f"fields stay LLM-driven.")


def _canon(value: object) -> str:
    """Order-stable, hashable canonical form so dict/list outputs are comparable."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _cap(text: str) -> str:
    if len(text) <= _INPUT_TEXT_CAP:
        return text
    return text[:_INPUT_TEXT_CAP] + f"\n...[truncated {len(text) - _INPUT_TEXT_CAP} chars]"
