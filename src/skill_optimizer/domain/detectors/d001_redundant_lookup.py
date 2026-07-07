"""D001 RedundantLookup — same (tool, input) repeated within a trace, recurring across traces."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime

from claude_agent_sdk import AssistantMessage, ToolUseBlock

from skill_optimizer.config.calibration import CALIBRATION
from skill_optimizer.config.pricing import Pricing
from skill_optimizer.domain.token_usage import TokenUsage
from skill_optimizer.domain.types import Finding
from skill_optimizer.ports.trace_store import CapturedRun

# Layer 1b: matches Bash commands that fetch a whole file's content (`cat <path>`).
# Conservatively rejects anything with flags, multi-file, or piped — those return
# different content slices and collapsing them would be a false positive.
_BASH_CAT_PATTERN = re.compile(r"^\s*cat\s+(\S+)\s*$")


def detect_redundant_lookups(
    skill_id: str,
    runs: list[CapturedRun],
    pricing: Pricing,
    min_occurrences: int = CALIBRATION.d001_min_occurrences,
    intra_trace_min: int = CALIBRATION.d001_intra_trace_min,
) -> list[Finding]:
    """D001: same (tool, input) repeated within a trace, recurring across ≥min_occurrences traces.

    Walks each trace's ToolUseBlocks, counts (tool_name, json(input)) pairs,
    flags any pair that hit count ≥ intra_trace_min in a single trace, then keeps
    only patterns that recurred across ≥ min_occurrences traces.
    """
    if not runs:
        return []

    pattern_traces: dict[tuple[str, str], list[CapturedRun]] = {}
    pattern_examples: dict[tuple[str, str], dict] = {}

    for run in runs:
        per_trace_counts: Counter[tuple[str, str]] = Counter()
        per_trace_first_use: dict[tuple[str, str], dict] = {}

        for msg in run.trace.messages:
            content = getattr(msg, "content", None)
            if not content:
                continue
            for block in content:
                if not isinstance(block, ToolUseBlock):
                    continue
                key = _semantic_key(block.name, block.input)
                per_trace_counts[key] += 1
                per_trace_first_use.setdefault(key, {
                    "tool_name": block.name,
                    "input": block.input,
                })

        for key, count in per_trace_counts.items():
            if count >= intra_trace_min:
                pattern_traces.setdefault(key, []).append(run)
                pattern_examples.setdefault(key, per_trace_first_use[key])

    pattern_evidence: list[dict] = []
    pattern_summaries: list[str] = []
    pattern_cost_pcts: list[float] = []
    pattern_latency_pcts: list[float] = []
    max_occurrences = 0

    for key, runs_with_pattern in pattern_traces.items():
        if len(runs_with_pattern) < min_occurrences:
            continue
        example = pattern_examples[key]
        tool_name = example["tool_name"]
        input_summary = _short_repr(example["input"])
        cost_pct, latency_pct = _estimate_redundant_lookup_savings(
            runs_with_pattern, key, pricing
        )
        pattern_evidence.append({
            "tool_name": tool_name,
            "input": example["input"],
            "input_summary": input_summary,
            "trace_refs": [f"run_{r.run_id:03d}.jsonl" for r in runs_with_pattern[:5]],
            "occurrences": len(runs_with_pattern),
            "estimated_cost_pct": cost_pct,
            "estimated_latency_pct": latency_pct,
        })
        pattern_summaries.append(f"{tool_name}({input_summary})")
        pattern_cost_pcts.append(cost_pct)
        pattern_latency_pcts.append(latency_pct)
        max_occurrences = max(max_occurrences, len(runs_with_pattern))

    if not pattern_evidence:
        return []

    ts = datetime.now(UTC).strftime("%Y-%m-%d")
    total_cost_pct = max(sum(pattern_cost_pcts), -99.0)
    avg_latency_pct = sum(pattern_latency_pcts) / len(pattern_latency_pcts)

    return [Finding(
        finding_id=f"skopt-{ts}-d001-{skill_id}-001",
        detector_id="D001",
        skill_id=skill_id,
        category="redundant_lookup",
        observed_pattern=(
            f"{len(pattern_evidence)} redundant-lookup pattern(s) detected: "
            f"{', '.join(pattern_summaries)}. Each called ≥{intra_trace_min}× per trace. "
            f"Recommendation: instruct the skill not to re-fetch the same inputs."
        ),
        evidence=tuple(pattern_evidence),
        estimated_cost_pct=total_cost_pct,
        estimated_latency_pct=avg_latency_pct,
        quality_risk="low",
        occurrences=max_occurrences,
    )]


def _semantic_key(tool_name: str, input_dict: dict | None) -> tuple[str, str]:
    """Map a tool call to a key, so "the same fetch done differently" counts as one.

    Two modes — and EVERY tool is covered:
      - FUZZY (Read and `cat`): the same *content* collapses even when the call
        looks different — a chunked read, or `cat` vs Read of the same file.
      - STRICT (every other tool): collapses only when the tool name AND the
        whole input are identical. Still catches an exact repeated call.

    What key each call produces:

        Read(file_path="policy.txt", offset=0)   -> ("__resource__", "file::policy.txt")            fuzzy
        Read(file_path="policy.txt", offset=40)  -> ("__resource__", "file::policy.txt")            same key (offset dropped)
        Bash("cat policy.txt")                   -> ("__resource__", "file::policy.txt")            same key as the Reads
        Bash("cat -n policy.txt")                -> ("Bash", '{"command":"cat -n policy.txt"}')     strict
        Bash("python validate.py")               -> ("Bash", '{"command":"python validate.py"}')    strict
        WebFetch(url="x.com")                    -> ("WebFetch", '{"url":"x.com"}')                 strict
        Grep(pattern="foo", path="y")            -> ("Grep", '{"path":"y","pattern":"foo"}')        strict

    The shared "__resource__" prefix is what lets a Read and a `cat` of the SAME
    file collapse onto one key — drop the tool name, it's the same resource.

    Only a bare `cat <path>` is fuzzy. `cat -n`, `head`, `tail`, multi-file, or
    piped/compound commands fall to strict — they return different content
    slices, so collapsing them would be a false positive.
    """
    if isinstance(input_dict, dict):
        if tool_name == "Read":
            return ("__resource__", f"file::{input_dict.get('file_path', '')}")
        if tool_name == "Bash":
            cmd = input_dict.get("command", "")
            if isinstance(cmd, str):
                match = _BASH_CAT_PATTERN.match(cmd)
                if match and not match.group(1).startswith("-"):
                    return ("__resource__", f"file::{match.group(1)}")
    return (tool_name, _canonical_input(input_dict))


def _canonical_input(inp: dict | None) -> str:
    if inp is None:
        return "null"
    try:
        return json.dumps(inp, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(inp)


def _short_repr(inp: dict | None, max_len: int = 80) -> str:
    s = _canonical_input(inp)
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


def _estimate_redundant_lookup_savings(
    runs_with_pattern: list[CapturedRun],
    pattern_key: tuple[str, str],
    pricing: Pricing,
) -> tuple[float, float]:
    """Predict (cost_pct, latency_pct) from collapsing redundant lookups to one.

    For each run hosting the pattern: simulate dropping all but the first
    assistant turn whose ToolUseBlocks match ``pattern_key``. Predicted cost =
    baseline trace cost − sum of dropped-turn costs. Latency proxy = dropped
    output_tokens / total output_tokens (output dominates wall-clock).

    Returns averages across runs as percentages (negative = savings).
    """
    cost_pcts: list[float] = []
    latency_pcts: list[float] = []
    for run in runs_with_pattern:
        trace = run.trace
        fallback_model = trace.models_used[0] if trace.models_used else ""

        baseline_cost = 0.0
        baseline_output_tokens = 0
        dropped_cost = 0.0
        dropped_output_tokens = 0
        seen_match = False

        for msg in trace.assistant_messages:
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

            if _message_matches(msg, pattern_key):
                if seen_match:
                    dropped_cost += turn_cost
                    dropped_output_tokens += usage.output_tokens
                else:
                    seen_match = True

        if baseline_cost > 0:
            cost_pcts.append(-(dropped_cost / baseline_cost) * 100.0)
        if baseline_output_tokens > 0:
            latency_pcts.append(-(dropped_output_tokens / baseline_output_tokens) * 100.0)

    cost_pct = sum(cost_pcts) / len(cost_pcts) if cost_pcts else 0.0
    latency_pct = sum(latency_pcts) / len(latency_pcts) if latency_pcts else 0.0
    return cost_pct, latency_pct


def _message_matches(msg: AssistantMessage, pattern_key: tuple[str, str]) -> bool:
    """True if the message contains a ToolUseBlock whose semantic key equals pattern_key."""
    content = getattr(msg, "content", None)
    if not content:
        return False
    for block in content:
        if not isinstance(block, ToolUseBlock):
            continue
        if _semantic_key(block.name, block.input) == pattern_key:
            return True
    return False
