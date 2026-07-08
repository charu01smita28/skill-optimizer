"""Tests for the calibration loader."""
from __future__ import annotations

from pathlib import Path

from skill_optimizer.config.calibration import Calibration, load_calibration


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    cal = load_calibration(tmp_path / "nope.yaml")
    assert cal == Calibration()
    assert cal.min_cost_win_pct == 10.0


def test_empty_yaml_returns_defaults(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("")
    assert load_calibration(p) == Calibration()


def test_partial_yaml_overlays_defaults(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("min_cost_win_pct: 5.0\nd007_min_chars: 999\n")
    cal = load_calibration(p)
    assert cal.min_cost_win_pct == 5.0       # overridden
    assert cal.d007_min_chars == 999         # overridden
    assert cal.d001_min_occurrences == 3     # default — not in the yaml


def test_unknown_keys_ignored(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("min_cost_win_pct: 7.0\nbogus_key: 123\n")
    cal = load_calibration(p)
    assert cal.min_cost_win_pct == 7.0
    assert not hasattr(cal, "bogus_key")


def test_shipped_calibration_yaml_loads() -> None:
    cal = load_calibration()  # the real config/calibration.yaml
    assert isinstance(cal, Calibration)
    assert cal.min_cost_win_pct == 10.0
