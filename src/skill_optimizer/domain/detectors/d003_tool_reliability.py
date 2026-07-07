"""D003: failed tool_use followed by similar same-tool retry; cross-trace gate by (tool, input-shape) signature."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher

from claude_agent_sdk import AssistantMessage, ToolResultBlock, ToolUseBlock

from skill_optimizer.config.calibration import CALIBRATION
from skill_optimizer.config.pricing import Pricing
from skill_optimizer.domain.token_usage import TokenUsage
from skill_optimizer.domain.types import Finding
from skill_optimizer.ports.trace_store import CapturedRun


@dataclass(frozen=True)
class _RetryPair:
    failed_msg_index: int
    retry_msg_index: int
    tool_name: str
    failed_input: dict
    retry_input: dict
    error_excerpt: str


def detect_tool_reliability_failures(
    skill_id: str,
    runs: list[CapturedRun],
    pricing: Pricing,
    min_occurrences: int = CALIBRATION.d003_min_occurrences,
    similarity_threshold: float = CALIBRATION.d003_similarity_threshold,
) -> list[Finding]:
    """Find recurring "a tool call failed, then was retried" patterns; emit one Finding.

    Flow:
        for each run:
            _find_retry_pairs  -> a call that ERRORED, then a SIMILAR retry of the SAME tool
                |
        group the pairs by signature = (tool_name, sorted input keys)   e.g. ("Bash","command")
                |
        keep signatures seen in >= min_occurrences DISTINCT runs   (default 2 — low, failures are rare)
                |   (none qualify -> return [])
        for each surviving signature:
              - take one sample pair as the concrete example
              - estimate saving = wasted failed-turn cost / total run cost
              - build an evidence entry (failed_input, retry_input, error_excerpt)
                |
        return ONE Finding(D003) listing every failure-retry pattern
    """
    if not runs:
        return []

    patterns_by_signature: dict[tuple[str, str], list[dict]] = {}
    for run in runs:
        for pair in _find_retry_pairs(
            run.trace.messages,
            run.trace.tool_results_by_use_id,
            similarity_threshold,
        ):
            sig = _failure_signature(pair)
            patterns_by_signature.setdefault(sig, []).append({"run": run, "pair": pair})

    qualifying = {
        sig: occ for sig, occ in patterns_by_signature.items()
        if len({o["run"].run_id for o in occ}) >= min_occurrences
    }
    if not qualifying:
        return []

    pattern_evidence: list[dict] = []
    pattern_summaries: list[str] = []
    pattern_cost_pcts: list[float] = []
    max_occurrences = 0

    for _sig, occurrences in qualifying.items():
        sample_pair = occurrences[0]["pair"]
        runs_with_pattern = [o["run"] for o in occurrences]
        cost_pct = _estimate_failure_savings(occurrences, pricing)
        pattern_evidence.append({
            "tool": sample_pair.tool_name,
            "failed_input": sample_pair.failed_input,
            "retry_input": sample_pair.retry_input,
            "error_excerpt": sample_pair.error_excerpt,
            "trace_refs": [f"run_{r.run_id:03d}.jsonl" for r in runs_with_pattern[:5]],
            "occurrences": len(runs_with_pattern),
            "estimated_cost_pct": cost_pct,
        })
        pattern_summaries.append(
            f"{sample_pair.tool_name}({_short_input(sample_pair.failed_input)}) → error → "
            f"{sample_pair.tool_name}({_short_input(sample_pair.retry_input)})"
        )
        pattern_cost_pcts.append(cost_pct)
        max_occurrences = max(max_occurrences, len(runs_with_pattern))

    ts = datetime.now(UTC).strftime("%Y-%m-%d")
    total_cost_pct = max(sum(pattern_cost_pcts), -99.0)

    return [Finding(
        finding_id=f"skopt-{ts}-d003-{skill_id}-001",
        detector_id="D003",
        skill_id=skill_id,
        category="tool_execution_misses",
        observed_pattern=(
            f"{len(pattern_evidence)} failure-retry pattern(s): "
            f"{'; '.join(pattern_summaries)}. Each pattern shows a tool call that "
            f"errored, followed by a similar retry of the same tool. "
            f"Recommendation: add SKILL.md guidance describing the failure mode "
            f"and the corrected approach so the model avoids the bad call upfront."
        ),
        evidence=tuple(pattern_evidence),
        estimated_cost_pct=total_cost_pct,
        estimated_latency_pct=total_cost_pct * 0.8,
        quality_risk="low",
        occurrences=max_occurrences,
    )]


def _find_retry_pairs(
    messages: tuple,
    tool_results_by_use_id: dict[str, ToolResultBlock],
    similarity_threshold: float,
) -> list[_RetryPair]:
    """Find (failed call -> similar same-tool retry) pairs in one trace.

        list every ToolUseBlock in order
        for each call C:
            result = tool_results_by_use_id[C.id]      # look up C's result by its id
            did C error? (result.is_error) ---- no ----> skip
                 | yes
                 v
            scan FORWARD for the first call R where:
                 R.name == C.name                          (same tool)
                 similarity(C.input, R.input) >= threshold (difflib ratio, default 0.5)
                 -> record _RetryPair(C, R, error)          (first match only, then stop)
    """
    tool_uses: list[tuple[int, ToolUseBlock]] = []
    msg_index = -1
    for msg in messages:
        if not isinstance(msg, AssistantMessage):
            continue
        msg_index += 1
        for block in (msg.content or []):
            if isinstance(block, ToolUseBlock):
                tool_uses.append((msg_index, block))

    pairs: list[_RetryPair] = []
    for i, (failed_idx, failed_block) in enumerate(tool_uses):
        result = tool_results_by_use_id.get(failed_block.id)
        if result is None or not getattr(result, "is_error", False):
            continue
        for retry_idx, retry_block in tool_uses[i + 1:]:
            if retry_block.name != failed_block.name:
                continue
            if _input_similarity(failed_block.input, retry_block.input) < similarity_threshold:
                continue
            pairs.append(_RetryPair(
                failed_msg_index=failed_idx,
                retry_msg_index=retry_idx,
                tool_name=failed_block.name,
                failed_input=dict(failed_block.input or {}),
                retry_input=dict(retry_block.input or {}),
                error_excerpt=_flatten_result_text(result)[:200],
            ))
            break
    return pairs


def _input_similarity(input1: dict | None, input2: dict | None) -> float:
    if not input1 and not input2:
        return 1.0
    s1 = json.dumps(input1 or {}, sort_keys=True)
    s2 = json.dumps(input2 or {}, sort_keys=True)
    if s1 == s2:
        return 1.0
    return SequenceMatcher(a=s1, b=s2).ratio()


def _failure_signature(pair: _RetryPair) -> tuple[str, str]:
    if not pair.failed_input:
        return (pair.tool_name, "")
    return (pair.tool_name, ",".join(sorted(pair.failed_input.keys())))


def _flatten_result_text(result: ToolResultBlock) -> str:
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


def _short_input(input_dict: dict) -> str:
    if not input_dict:
        return "{}"
    s = json.dumps(input_dict, sort_keys=True)
    return s if len(s) <= 60 else s[:57] + "..."


def _estimate_failure_savings(occurrences: list[dict], pricing: Pricing) -> float:
    cost_pcts: list[float] = []
    for entry in occurrences:
        run = entry["run"]
        pair = entry["pair"]
        trace = run.trace
        fallback_model = trace.models_used[0] if trace.models_used else ""
        baseline_cost = 0.0
        failed_turn_cost = 0.0

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
            if msg_index == pair.failed_msg_index:
                failed_turn_cost = turn_cost

        if baseline_cost > 0:
            cost_pcts.append(-(failed_turn_cost / baseline_cost) * 100.0)

    return sum(cost_pcts) / len(cost_pcts) if cost_pcts else 0.0
