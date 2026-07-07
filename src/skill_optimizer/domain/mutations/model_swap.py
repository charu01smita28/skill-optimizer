"""model_swap Tier-A mutation — pairs with D004 (ModelTier).

Imports the model-tier policy data (`_DOWNGRADE_PATH`, `_TARGET_MODEL`) from the
D004 detector module, since detection is the upstream concept that owns the policy.
"""
from __future__ import annotations

from skill_optimizer.domain.detectors.d004_model_tier import (
    _DOWNGRADE_PATH,
    _TARGET_MODEL,
)
from skill_optimizer.domain.types import Finding, Patch, Proposal


def propose_model_swap(finding: Finding, current_skill_text: str) -> Proposal | None:
    """Tier-A: swap the SKILL.md `model:` line one tier cheaper per `_DOWNGRADE_PATH`.

    Returns ``None`` when the skill already declares the target model — this
    happens when the runtime ignored a frontmatter declaration and ran a higher
    tier anyway, so D004 saw waste in the trace but the skill itself is already
    configured correctly.
    """
    if f"model: {_TARGET_MODEL}" in current_skill_text:
        return None

    before, after, description = "", f"model: {_TARGET_MODEL}", \
        f"Insert `model: {_TARGET_MODEL}` into SKILL.md frontmatter"

    for large, target in _DOWNGRADE_PATH.items():
        line = f"model: {large}"
        if line in current_skill_text:
            before = line
            after = f"model: {target}"
            description = f"Swap declared model {large} → {target}"
            break

    patch = Patch(
        target_relative_path="SKILL.md",
        before_text=before,
        after_text=after,
        description=description,
    )
    return Proposal(
        proposal_id=f"{finding.finding_id}-prop",
        finding=finding,
        patch=patch,
        tier="1",
        mutation_type="model_swap",
    )
