"""D008: detect consecutive single-tool turns that could be one parallel turn.

Named ``pseudoparallel`` because the Claude Code non-interactive runtime serializes
tool_use emission regardless of system-prompt directives (verified via the
Anthropic-docs ``<use_parallel_tool_calls>`` block — see ``scripts/probe_parallelism.py``).
The Tier-2 rewriter still produces cost wins on patched traces — empirically via prose
perturbation (shorter / more direct workflow) rather than actual concurrent execution.

Scope: {Read, Glob, Grep} only (read-only, safe to reorder). Splits a batch on
substring data-dependency. Cross-trace gate: same signature in ≥min_occurrences
traces.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ToolResultBlock, ToolUseBlock

from skill_optimizer.config.calibration import CALIBRATION
from skill_optimizer.config.pricing import Pricing
from skill_optimizer.domain.token_usage import TokenUsage
from skill_optimizer.domain.types import Finding
from skill_optimizer.ports.trace_store import CapturedRun

# Read-only tools — safe to reorder.
SAFE_TOOLS = frozenset({"Read", "Glob", "Grep"})


@dataclass(frozen=True)
class _Turn:
    msg_index: int
    tool_name: str
    tool_use_id: str
    input: dict
    identifier: str  # file_path or pattern


def detect_pseudoparallelizable_tool_calls(
    skill_id: str,
    runs: list[CapturedRun],
    pricing: Pricing,
    min_occurrences: int = CALIBRATION.d008_min_occurrences,
) -> list[Finding]:
    """Emit one consolidated Finding per skill listing every batchable group."""
    if not runs:
        return []

    batches_by_signature: dict[tuple[str, ...], list[dict]] = {}
    for run in runs:
        for batch in _find_independent_batches(run.trace.messages, run.trace.tool_results_by_use_id):
            signature = _basename_signature(batch)
            batches_by_signature.setdefault(signature, []).append({
                "run": run,
                "batch": batch,
            })

    qualifying = {
        sig: occ for sig, occ in batches_by_signature.items()
        if len({o["run"].run_id for o in occ}) >= min_occurrences
    }
    if not qualifying:
        return []

    pattern_evidence: list[dict] = []
    pattern_summaries: list[str] = []
    pattern_cost_pcts: list[float] = []
    pattern_latency_pcts: list[float] = []
    max_occurrences = 0

    for signature, occurrences in qualifying.items():
        sample_batch = occurrences[0]["batch"]
        runs_with_pattern = [o["run"] for o in occurrences]
        cost_pct, latency_pct = _estimate_pseudoparallelization_savings(
            occurrences, pricing
        )
        pattern_evidence.append({
            "tools": [t.tool_name for t in sample_batch],
            "identifiers": [t.identifier for t in sample_batch],
            "inputs": [t.input for t in sample_batch],
            "trace_refs": [f"run_{r.run_id:03d}.jsonl" for r in runs_with_pattern[:5]],
            "occurrences": len(runs_with_pattern),
            "estimated_cost_pct": cost_pct,
            "estimated_latency_pct": latency_pct,
        })
        pattern_summaries.append(
            f"[{', '.join(t.tool_name + '(' + Path(t.identifier).name + ')' for t in sample_batch)}]"
        )
        pattern_cost_pcts.append(cost_pct)
        pattern_latency_pcts.append(latency_pct)
        max_occurrences = max(max_occurrences, len(runs_with_pattern))

    ts = datetime.now(UTC).strftime("%Y-%m-%d")
    total_cost_pct = max(sum(pattern_cost_pcts), -99.0)
    avg_latency_pct = sum(pattern_latency_pcts) / len(pattern_latency_pcts)

    return [Finding(
        finding_id=f"skopt-{ts}-d008-{skill_id}-001",
        detector_id="D008",
        skill_id=skill_id,
        category="pseudoparallelizable_tool_calls",
        observed_pattern=(
            f"{len(pattern_evidence)} parallelizable batch(es): "
            f"{', '.join(pattern_summaries)}. Each runs as ≥2 sequential single-tool "
            f"turns when one parallel turn would suffice. "
            f"Recommendation: instruct the skill to invoke independent tool calls in parallel."
        ),
        evidence=tuple(pattern_evidence),
        estimated_cost_pct=total_cost_pct,
        estimated_latency_pct=avg_latency_pct,
        quality_risk="low",
        occurrences=max_occurrences,
    )]


def _find_independent_batches(
    messages: tuple,
    tool_results_by_use_id: dict[str, ToolResultBlock],
) -> list[list[_Turn]]:
    """Return all batches of ≥2 truly-independent single-tool turns."""
    candidate = _collect_consecutive_single_tool_turns(messages)
    if not candidate:
        return []
    return [
        sub_batch
        for run in candidate
        for sub_batch in _split_on_dependencies(run, tool_results_by_use_id)
        if len(sub_batch) >= 2
    ]


def _collect_consecutive_single_tool_turns(messages: tuple) -> list[list[_Turn]]:
    """Maximal runs of consecutive single-safe-tool turns. Non-qualifying turns break the run."""
    runs: list[list[_Turn]] = []
    current: list[_Turn] = []

    msg_index = -1
    for msg in messages:
        if not isinstance(msg, AssistantMessage):
            continue
        msg_index += 1
        turn = _as_single_safe_turn(msg, msg_index)
        if turn is None:
            if current:
                runs.append(current)
                current = []
            continue
        current.append(turn)

    if current:
        runs.append(current)
    return runs


def _as_single_safe_turn(msg: AssistantMessage, msg_index: int) -> _Turn | None:
    tool_uses = [b for b in (msg.content or []) if isinstance(b, ToolUseBlock)]
    if len(tool_uses) != 1:
        return None
    block = tool_uses[0]
    if block.name not in SAFE_TOOLS:
        return None
    identifier = _primary_identifier(block.name, block.input)
    if not identifier:
        return None
    return _Turn(
        msg_index=msg_index,
        tool_name=block.name,
        tool_use_id=block.id,
        input=block.input or {},
        identifier=identifier,
    )


def _primary_identifier(tool_name: str, input_dict: dict | None) -> str:
    if not isinstance(input_dict, dict):
        return ""
    if tool_name == "Read":
        return str(input_dict.get("file_path", ""))
    if tool_name in ("Glob", "Grep"):
        return str(input_dict.get("pattern", ""))
    return ""


def _split_on_dependencies(
    batch: list[_Turn],
    tool_results_by_use_id: dict[str, ToolResultBlock],
) -> list[list[_Turn]]:
    """Split where turn N+1's identifier appears in turn N's tool_result."""
    if not batch:
        return []
    sub_batches: list[list[_Turn]] = []
    current = [batch[0]]
    for prev, nxt in zip(batch, batch[1:]):
        if _depends_on(prev, nxt, tool_results_by_use_id):
            sub_batches.append(current)
            current = [nxt]
        else:
            current.append(nxt)
    sub_batches.append(current)
    return sub_batches


def _depends_on(
    prev: _Turn,
    nxt: _Turn,
    tool_results_by_use_id: dict[str, ToolResultBlock],
) -> bool:
    result = tool_results_by_use_id.get(prev.tool_use_id)
    if result is None:
        return False  # no result captured: lean independent
    haystack = _flatten_result_text(result)
    if not haystack:
        return False
    needle_full = nxt.identifier
    needle_basename = Path(needle_full).name
    return needle_basename in haystack or needle_full in haystack


def _flatten_result_text(result: ToolResultBlock) -> str:
    """Flatten ToolResultBlock.content (str | list[block] | None) to one string."""
    content = result.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            elif hasattr(item, "text"):
                parts.append(str(getattr(item, "text", "")))
        return "\n".join(parts)
    return ""


def _basename_signature(batch: list[_Turn]) -> tuple[str, ...]:
    """Sorted (tool, basename) tuple — order-independent across traces."""
    return tuple(sorted(
        f"{t.tool_name}::{Path(t.identifier).name}" for t in batch
    ))


def _estimate_pseudoparallelization_savings(
    occurrences: list[dict],
    pricing: Pricing,
) -> tuple[float, float]:
    """Saved per run ≈ sum_of_batched_costs - max_of_batched_costs.

    A parallel turn pays roughly the cost of the largest single sequential turn.
    """
    cost_pcts: list[float] = []
    latency_pcts: list[float] = []
    for entry in occurrences:
        run = entry["run"]
        batch = entry["batch"]
        trace = run.trace
        fallback_model = trace.models_used[0] if trace.models_used else ""
        baseline_cost = 0.0
        baseline_output_tokens = 0
        batched_costs: list[float] = []
        batched_output_tokens: list[int] = []

        batch_msg_indices = {t.msg_index for t in batch}

        msg_index = -1
        for msg in trace.messages:
            if not isinstance(msg, AssistantMessage):
                continue
            msg_index += 1
            model = msg.model or fallback_model
            usage = TokenUsage.from_usage_dict(msg.usage)
            if not model:
                continue
            try:
                rates = pricing.rates_for(model)
            except KeyError:
                continue
            turn_cost = usage.cost_at(rates)
            baseline_cost += turn_cost
            baseline_output_tokens += usage.output_tokens
            if msg_index in batch_msg_indices:
                batched_costs.append(turn_cost)
                batched_output_tokens.append(usage.output_tokens)

        if baseline_cost > 0 and batched_costs:
            saved_cost = sum(batched_costs) - max(batched_costs)
            cost_pcts.append(-(saved_cost / baseline_cost) * 100.0)
        if baseline_output_tokens > 0 and batched_output_tokens:
            saved_out = sum(batched_output_tokens) - max(batched_output_tokens)
            latency_pcts.append(-(saved_out / baseline_output_tokens) * 100.0)

    cost_pct = sum(cost_pcts) / len(cost_pcts) if cost_pcts else 0.0
    latency_pct = sum(latency_pcts) / len(latency_pcts) if latency_pcts else 0.0
    return cost_pct, latency_pct
