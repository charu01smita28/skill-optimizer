"""Filesystem implementation of TraceStore — lenient about layout.

Two paths:

1. **Captured layout** (what ``scripts/capture_traces.py`` writes):

       <baseline_dir>/manifest.json
       <baseline_dir>/run_NNN.jsonl
       <baseline_dir>/run_NNN.output.json   (optional — extracted from JSONL if missing)

2. **Bring-your-own layout** (what gets dropped in directly):

       <baseline_dir>/<anything>.jsonl
       <baseline_dir>/<anything>.jsonl
       ...

   No manifest, no parallel ``output.json`` files. The store enumerates
   ``*.jsonl``, parses each into a ``Trace``, and pulls the structured output
   from the trace's Write tool call to ``output.json``.

The captured layout is preserved so existing demo traces work unchanged; the
BYO layout removes every requirement except the JSONL itself.
"""
from __future__ import annotations

import json
from pathlib import Path

from skill_optimizer.domain.trace import (
    Trace,
    extract_input_filename,
    extract_output_from_trace,
    parse_trace_file,
)
from skill_optimizer.ports.trace_store import CapturedRun


class JsonlTraceStore:
    def __init__(
        self,
        baseline_dir: Path,
        sample_inputs_dir: Path,
        output_filename: str = "output.json",
    ) -> None:
        self._baseline_dir = baseline_dir
        self._sample_inputs_dir = sample_inputs_dir
        self._output_filename = output_filename

    def list_runs(self) -> list[CapturedRun]:
        manifest_path = self._baseline_dir / "manifest.json"
        if manifest_path.exists():
            return self._read_from_manifest(manifest_path)
        return self._synthesize_from_jsonls()

    # ---- captured layout -----------------------------------------------------

    def _read_from_manifest(self, manifest_path: Path) -> list[CapturedRun]:
        manifest = json.loads(manifest_path.read_text())
        runs: list[CapturedRun] = []
        for entry in manifest.get("runs", []):
            if entry.get("status") != "ok":
                continue
            trace_path = self._baseline_dir / entry["trace"]
            if not trace_path.exists():
                continue

            trace = parse_trace_file(trace_path)
            output = self._resolve_output(entry, trace)
            if output is None:
                continue  # skill never wrote output.json — nothing to compare

            input_filename = entry["input"]
            input_text = self._read_input_text(input_filename)
            runs.append(
                CapturedRun(
                    run_id=int(entry["run"]),
                    input_filename=input_filename,
                    input_text=input_text,
                    output=output,
                    trace=trace,
                    elapsed_s=float(entry.get("elapsed_s", 0.0)),
                )
            )
        runs.sort(key=lambda r: r.run_id)
        return runs

    def _resolve_output(self, entry: dict, trace: Trace) -> dict | None:
        """Prefer the parallel output.json; fall back to extracting from the JSONL."""
        output_name = entry.get("output")
        if output_name:
            output_path = self._baseline_dir / output_name
            if output_path.exists():
                try:
                    return json.loads(output_path.read_text())
                except json.JSONDecodeError:
                    pass
        return extract_output_from_trace(trace, output_filename=self._output_filename)

    # ---- bring-your-own layout -----------------------------------------------

    def _synthesize_from_jsonls(self) -> list[CapturedRun]:
        if not self._baseline_dir.exists():
            return []
        jsonl_paths = sorted(p for p in self._baseline_dir.glob("*.jsonl") if p.is_file())
        runs: list[CapturedRun] = []
        for run_id, trace_path in enumerate(jsonl_paths, start=1):
            try:
                trace = parse_trace_file(trace_path)
            except (ValueError, json.JSONDecodeError):
                continue
            output = extract_output_from_trace(trace, output_filename=self._output_filename)
            if output is None:
                continue
            input_filename = extract_input_filename(trace, fallback=trace_path.stem)
            input_text = self._read_input_text(input_filename)
            runs.append(
                CapturedRun(
                    run_id=run_id,
                    input_filename=input_filename,
                    input_text=input_text,
                    output=output,
                    trace=trace,
                    elapsed_s=0.0,  # no manifest → latency reporting unavailable
                )
            )
        return runs

    # ---- shared --------------------------------------------------------------

    def _read_input_text(self, input_filename: str) -> str:
        input_path = self._sample_inputs_dir / input_filename
        if input_path.is_file():
            return input_path.read_text()
        return ""
