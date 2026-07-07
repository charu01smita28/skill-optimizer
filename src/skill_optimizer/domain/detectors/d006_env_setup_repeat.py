"""D006: recurring install/download Bash patterns; cross-trace gate by (family, normalized target)."""
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

_FLAG = r"(?:-{1,2}[A-Za-z][A-Za-z0-9\-]*\s+)*"

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pip_install",   re.compile(rf"\bpip(?:3)?\s+install\s+{_FLAG}([^\s|;&]+)")),
    ("npm_install",   re.compile(rf"\bnpm\s+install\s+{_FLAG}([^\s|;&]+)?")),
    ("apt_install",   re.compile(rf"\bapt(?:-get)?\s+install\s+{_FLAG}([^\s|;&]+)")),
    ("brew_install",  re.compile(rf"\bbrew\s+install\s+{_FLAG}([^\s|;&]+)")),
    ("curl_download", re.compile(rf"\bcurl\s+{_FLAG}([^\s|;&]+)")),
    ("wget_download", re.compile(rf"\bwget\s+{_FLAG}([^\s|;&]+)")),
)


@dataclass(frozen=True)
class _SetupCall:
    msg_index: int
    family: str
    target: str
    command: str
    tool_use_id: str


def detect_env_setup_repeat(
    skill_id: str,
    runs: list[CapturedRun],
    pricing: Pricing,
    min_occurrences: int = CALIBRATION.d006_min_occurrences,
) -> list[Finding]:
    if not runs:
        return []

    patterns_by_signature: dict[tuple[str, str], list[dict]] = {}
    for run in runs:
        for setup in _find_setup_calls(run.trace.messages):
            sig = (setup.family, setup.target)
            patterns_by_signature.setdefault(sig, []).append({"run": run, "setup": setup})

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

    for (family, target), occurrences in qualifying.items():
        sample_setup = occurrences[0]["setup"]
        runs_with_pattern = [o["run"] for o in occurrences]
        cost_pct = _estimate_setup_savings(occurrences, pricing)
        pattern_evidence.append({
            "family": family,
            "target": target,
            "command_excerpt": sample_setup.command[:200],
            "trace_refs": [f"run_{r.run_id:03d}.jsonl" for r in runs_with_pattern[:5]],
            "occurrences": len(runs_with_pattern),
            "estimated_cost_pct": cost_pct,
        })
        pattern_summaries.append(f"{family}({target})")
        pattern_cost_pcts.append(cost_pct)
        max_occurrences = max(max_occurrences, len(runs_with_pattern))

    ts = datetime.now(UTC).strftime("%Y-%m-%d")
    total_cost_pct = max(sum(pattern_cost_pcts), -99.0)

    return [Finding(
        finding_id=f"skopt-{ts}-d006-{skill_id}-001",
        detector_id="D006",
        skill_id=skill_id,
        category="env_setup_repeat",
        observed_pattern=(
            f"{len(pattern_evidence)} repeated env-setup operation(s): "
            f"{', '.join(pattern_summaries)}. Each install/download command "
            f"recurs across runs and rebuilds environment state every time. "
            f"Recommendation: rewrite SKILL.md to assume the environment is "
            f"pre-provisioned, or to check-then-install only when missing."
        ),
        evidence=tuple(pattern_evidence),
        estimated_cost_pct=total_cost_pct,
        estimated_latency_pct=total_cost_pct * 0.8,
        quality_risk="low",
        occurrences=max_occurrences,
    )]


def _find_setup_calls(messages: tuple) -> list[_SetupCall]:
    out: list[_SetupCall] = []
    msg_index = -1
    for msg in messages:
        if not isinstance(msg, AssistantMessage):
            continue
        msg_index += 1
        for block in (msg.content or []):
            if not isinstance(block, ToolUseBlock) or block.name != "Bash":
                continue
            command = (block.input or {}).get("command", "")
            if not isinstance(command, str) or not command.strip():
                continue
            family_target = _classify_command(command)
            if family_target is None:
                continue
            family, target = family_target
            out.append(_SetupCall(
                msg_index=msg_index,
                family=family,
                target=target,
                command=command,
                tool_use_id=block.id,
            ))
    return out


def _classify_command(command: str) -> tuple[str, str] | None:
    for family, pattern in _PATTERNS:
        match = pattern.search(command)
        if not match:
            continue
        target = (match.group(1) or "").strip().strip('"').strip("'") or "(unspecified)"
        return family, _normalize_target(family, target)
    return None

# installs:   target.lower().split("==")[0].split("@")[0]   # pandas==2.0, Pandas, pandas@latest → "pandas"
#   downloads:  target.split("?")[0].rstrip("/")              # …/x.json?sig=abc, …/x.json/ → "…/x.json"

def _normalize_target(family: str, target: str) -> str:
    if family in ("pip_install", "apt_install", "brew_install", "npm_install"):
        return target.lower().split("==")[0].split("@")[0]
    if family in ("curl_download", "wget_download"):
        return target.split("?")[0].rstrip("/")
    return target


def _estimate_setup_savings(occurrences: list[dict], pricing: Pricing) -> float:
    cost_pcts: list[float] = []
    for entry in occurrences:
        run = entry["run"]
        setup = entry["setup"]
        trace = run.trace
        fallback_model = trace.models_used[0] if trace.models_used else ""
        baseline_cost = 0.0
        setup_turn_cost = 0.0

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
            if msg_index == setup.msg_index:
                setup_turn_cost = turn_cost

        if baseline_cost > 0:
            cost_pcts.append(-(setup_turn_cost / baseline_cost) * 100.0)

    return sum(cost_pcts) / len(cost_pcts) if cost_pcts else 0.0
