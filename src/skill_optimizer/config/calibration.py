"""Loader for ``config/calibration.yaml`` — tunable thresholds (decide()'s cost
gate, detector occurrence floors, D007 size/trim, verifier replay params). Baked-in
defaults here; the yaml overrides them. ``CALIBRATION`` loads once at import.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

import yaml

_DEFAULT_CALIBRATION_PATH = Path(__file__).resolve().parents[3] / "config" / "calibration.yaml"


@dataclass(frozen=True)
class Calibration:
    min_cost_win_pct: float = 10.0
    d001_min_occurrences: int = 3
    d001_intra_trace_min: int = 2
    d003_min_occurrences: int = 2
    d003_similarity_threshold: float = 0.5
    d004_min_occurrences: int = 3
    d004_tier_latency_pct: float = -40.0
    d005_min_inputs: int = 2
    d005_min_replays: int = 2
    d006_min_occurrences: int = 2
    d007_min_chars: int = 1800
    d007_trim_fraction: float = 0.25
    d008_min_occurrences: int = 3
    d012_min_occurrences: int = 3
    verifier_n_replays: int = 3
    verifier_replay_timeout_s: int = 240


def load_calibration(path: Path | None = None) -> Calibration:
    """Parse calibration.yaml over the baked-in defaults. Missing file or missing
    keys → defaults; unknown keys are ignored.
    """
    src = path or _DEFAULT_CALIBRATION_PATH
    if not src.exists():
        return Calibration()
    raw = yaml.safe_load(src.read_text()) or {}
    known = {f.name for f in fields(Calibration)}
    return Calibration(**{k: v for k, v in raw.items() if k in known})


CALIBRATION = load_calibration()
