"""D012: the model re-derives the same ad-hoc script on every run instead of invoking a
persisted artifact; cross-trace gate by the script's recurring function name.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from claude_agent_sdk import AssistantMessage, ToolUseBlock

from skill_optimizer.config.calibration import CALIBRATION
from skill_optimizer.config.pricing import Pricing
from skill_optimizer.domain.token_usage import TokenUsage
from skill_optimizer.domain.types import Finding
from skill_optimizer.ports.trace_store import CapturedRun

_DEF_NAME = re.compile(r"^[ \t]*def[ \t]+(\w+)", re.M)
_MAX_CODE_CHARS = 6000      # per-artifact code cap in evidence (helper_extract reads it)
_MAX_EVIDENCE_RUNS = 6

#  So an "artifact" is just "one spot where the model wrote a function."
@dataclass(frozen=True)
class _ScriptArtifact:
    msg_index: int # which assistant message it was in
    origin: str  # "write": a Write of a .py file (clean source) | "bash": inline python
    code: str 
    def_names: frozenset[str] #the set of function names found in it


def detect_script_rederivation(
    skill_id: str,
    runs: list[CapturedRun],
    pricing: Pricing,
    min_occurrences: int = CALIBRATION.d012_min_occurrences,
) -> list[Finding]:
    """Emit a Finding when the same function is authored from scratch in ≥min_occurrences runs."""
    if not runs:
        return []

    artifacts_by_run: dict[int, list[_ScriptArtifact]] = {}
    for run in runs:
        arts = _find_script_artifacts(run.trace.messages)
        if arts:
            artifacts_by_run[run.run_id] = arts
    # eg  artifacts_by_run = {1: [bash+write for validate_invoice], 2: [...], ...}.

    #  - flip to get function name → the set of run_ids that authored it.
    runs_per_name: dict[str, set[int]] = {}
    for run_id, arts in artifacts_by_run.items():
        for art in arts:
            for name in art.def_names:
                runs_per_name.setdefault(name, set()).add(run_id)
    # eg runs_per_name = {"validate_invoice": {1, 2, 3, …, 58}}.

    recurring = {n: rids for n, rids in runs_per_name.items() if len(rids) >= min_occurrences}
    if not recurring:
        return []

    # The "script" is the most-recurring function name (alphabetical tiebreak for determinism);
    # other names that co-occur with it are its sub-helpers, reported but not separately flagged.
    primary = sorted(recurring, key=lambda n: (-len(recurring[n]), n))[0]
    script_run_ids = recurring[primary]
    sub_helpers = sorted(n for n in recurring if n != primary and recurring[n] & script_run_ids)

    #   - Build reps = representative examples, one per run that authored the primary.
    reps: list[tuple[CapturedRun, _ScriptArtifact, list[int]]] = []
    for run in runs:
        if run.run_id not in script_run_ids:
            continue
        with_primary = [a for a in artifacts_by_run[run.run_id] if primary in a.def_names]
        with_primary.sort(key=lambda a: (a.origin != "write", a.msg_index))  # prefer a Write
        reps.append((run, with_primary[0], [a.msg_index for a in with_primary]))

    evidence = tuple(
        {
            "trace_ref": f"run_{run.run_id:03d}.jsonl",
            "origin": art.origin,
            "def_names": sorted(art.def_names),
            "code": art.code[:_MAX_CODE_CHARS],
            "fragment": art.code[:300],
        }
        for run, art, _ in reps[:_MAX_EVIDENCE_RUNS]
    )
    # calling cost helper to estimate the saving
    cost_pct = _estimate_rederivation_savings(reps, pricing)

    ts = datetime.now(UTC).strftime("%Y-%m-%d")
    helper_note = f", plus recurring helper(s) {', '.join(sub_helpers)}," if sub_helpers else ""
    return [Finding(
        finding_id=f"skopt-{ts}-d012-{skill_id}-001",
        detector_id="D012",
        skill_id=skill_id,
        category="script_rederivation",
        observed_pattern=(
            f"The model re-derives `{primary}(...)`{helper_note} from scratch in "
            f"{len(script_run_ids)} of {len(runs)} runs — authored via Write and/or inline "
            f"`python` each time rather than invoked from a persisted file. Recommendation: "
            f"extract it once into a callable `helper.py` and have SKILL.md invoke that."
        ),
        evidence=evidence,
        estimated_cost_pct=cost_pct,
        estimated_latency_pct=cost_pct * 0.7,
        quality_risk="low",
        occurrences=len(script_run_ids),
    )]


def _find_script_artifacts(messages: tuple) -> list[_ScriptArtifact]:
    out: list[_ScriptArtifact] = []
    msg_index = -1
    for msg in messages:
        if not isinstance(msg, AssistantMessage):
            continue
        msg_index += 1
        for block in (msg.content or []):
            if not isinstance(block, ToolUseBlock):
                continue
            inp = block.input or {}
            if block.name == "Write":
                path = inp.get("file_path", "")
                code = inp.get("content", "")
                if not (isinstance(path, str) and path.endswith(".py") and isinstance(code, str)):
                    continue
                names = frozenset(_DEF_NAME.findall(code))
                if names:
                    out.append(_ScriptArtifact(msg_index, "write", code, names))
            elif block.name == "Bash":
                cmd = inp.get("command", "")
                if not isinstance(cmd, str):
                    continue
                names = frozenset(_DEF_NAME.findall(cmd))
                if names:  # a `def` inside a Bash command ⇒ Python being authored inline
                    out.append(_ScriptArtifact(msg_index, "bash", cmd, names))
    return out


# what fraction of a run's cost was spent authoring the script?
def _estimate_rederivation_savings(
    reps: list[tuple[CapturedRun, _ScriptArtifact, list[int]]], pricing: Pricing
) -> float:
    """Per run, the share of trace cost spent on the turns that (re)authored the script,
    averaged and negated. Conservative — assumes extraction zeroes those turns' marginal cost.
    """
    cost_pcts: list[float] = []
    for run, _, script_msg_indices in reps:
        trace = run.trace
        fallback_model = trace.models_used[0] if trace.models_used else ""
        total = 0.0
        script_cost = 0.0
        msg_index = -1
        for msg in trace.messages:
            if not isinstance(msg, AssistantMessage):
                continue
            msg_index += 1
            model = msg.model or fallback_model
            if not model:
                continue
            try:
                rates = pricing.rates_for(model)
            except KeyError:
                continue
            turn_cost = TokenUsage.from_usage_dict(msg.usage).cost_at(rates)
            total += turn_cost
            if msg_index in script_msg_indices:
                script_cost += turn_cost
        if total > 0:
            cost_pcts.append(-(script_cost / total) * 100.0)
    return sum(cost_pcts) / len(cost_pcts) if cost_pcts else 0.0
# please see an example of the cost calculation for this in @private/interview-cost.md file 