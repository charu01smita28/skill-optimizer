"""D007: a SKILL.md large enough to be worth a tightening pass. Heuristic gate
(char count); the paired `prompt_rewrite` mutation does the actual trimming and
the verifier confirms behavior is unchanged.
"""
from __future__ import annotations

from datetime import UTC, datetime

from skill_optimizer.config.calibration import CALIBRATION
from skill_optimizer.config.pricing import Pricing
from skill_optimizer.domain.types import Finding
from skill_optimizer.ports.trace_store import CapturedRun


def detect_verbose_prompt(
    skill_id: str,
    skill_md_text: str,
    runs: list[CapturedRun],
    pricing: Pricing | None = None,  # unused; kept for detector-call uniformity
    min_chars: int = CALIBRATION.d007_min_chars,
) -> list[Finding]:
    """Flag a SKILL.md over ``min_chars`` as a prompt-tightening candidate."""
    if not runs or len(skill_md_text) < min_chars:
        return []

    sections = _split_sections(skill_md_text)
    biggest_heading, biggest_body = max(sections, key=lambda s: len(s[1]))
    section_evidence = [
        {"heading": h, "chars": len(b)} for h, b in sections
    ] + [{"skill_md_chars": len(skill_md_text), "n_runs": len(runs)}]

    cost_pct = _estimate_tightening_savings(skill_md_text, runs)
    ts = datetime.now(UTC).strftime("%Y-%m-%d")
    return [Finding(
        finding_id=f"skopt-{ts}-d007-{skill_id}-001",
        detector_id="D007",
        skill_id=skill_id,
        category="verbose_prompt",
        observed_pattern=(
            f"SKILL.md is {len(skill_md_text)} chars across {len(sections)} section(s) "
            f"(largest: '{biggest_heading}' — {len(biggest_body)} chars). Candidate for a "
            f"tightening pass: trim ornamental/redundant prose, keep load-bearing "
            f"instructions; the verifier confirms output is unchanged or the patch is rejected."
        ),
        evidence=tuple(section_evidence),
        estimated_cost_pct=cost_pct,
        estimated_latency_pct=round(cost_pct * 1.2, 1),
        quality_risk="medium",
        occurrences=len(runs),
    )]


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split a SKILL.md into (heading, body) pairs on level-2 headings. Everything
    before the first ``## `` (frontmatter, H1, intro) is the ``(preamble)`` section.
    """
    sections: list[tuple[str, str]] = []
    heading = "(preamble)"
    body_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            sections.append((heading, "\n".join(body_lines)))
            heading = line[3:].strip()
            body_lines = []
        else:
            body_lines.append(line)
    sections.append((heading, "\n".join(body_lines)))
    return sections


def _estimate_tightening_savings(skill_md_text: str, runs: list[CapturedRun]) -> float:
    """Heuristic — SKILL.md's token share of an average run's input × the trimmable
    fraction. Small in reality (SKILL.md is cache-routed); the verifier measures it."""
    skill_md_tokens = max(len(skill_md_text) // 4, 1)
    inputs = [r.trace.total_input_tokens for r in runs if r.trace.total_input_tokens > 0]
    if not inputs:
        return -2.0
    avg_input = sum(inputs) / len(inputs)
    frac = min(skill_md_tokens / avg_input, 1.0)
    return -round(frac * CALIBRATION.d007_trim_fraction * 100.0, 1)
