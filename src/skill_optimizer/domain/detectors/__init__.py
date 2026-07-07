"""Per-detector modules. One file per detector function."""
from skill_optimizer.domain.detectors.d001_redundant_lookup import detect_redundant_lookups
from skill_optimizer.domain.detectors.d003_tool_reliability import detect_tool_reliability_failures
from skill_optimizer.domain.detectors.d004_model_tier import detect_model_tier_overkill
from skill_optimizer.domain.detectors.d005_determinism import detect_deterministic_steps
from skill_optimizer.domain.detectors.d006_env_setup_repeat import detect_env_setup_repeat
from skill_optimizer.domain.detectors.d007_prompt_tightening import detect_verbose_prompt
from skill_optimizer.domain.detectors.d008_pseudoparallelization import detect_pseudoparallelizable_tool_calls
from skill_optimizer.domain.detectors.d012_script_rederivation import detect_script_rederivation

__all__ = [
    "detect_redundant_lookups",
    "detect_tool_reliability_failures",
    "detect_model_tier_overkill",
    "detect_deterministic_steps",
    "detect_env_setup_repeat",
    "detect_verbose_prompt",
    "detect_pseudoparallelizable_tool_calls",
    "detect_script_rederivation",
]
