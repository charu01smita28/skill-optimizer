"""Port: read captured baseline runs.

A "run" is what `scripts/capture_traces.py` produces: a JSONL trace plus a
parsed output.json. The optimizer ingests pairs of (trace, output) keyed by
the input filename.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from skill_optimizer.domain.trace import Trace


@dataclass(frozen=True)
class CapturedRun:
    run_id: int
    input_filename: str
    input_text: str
    output: dict
    trace: Trace
    elapsed_s: float


class TraceStore(Protocol):
    def list_runs(self) -> list[CapturedRun]: ...
