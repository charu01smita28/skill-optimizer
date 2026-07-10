# Limitations

The system's gates — equivalence preservation, signed-band cost thresholds — are deterministic and reproducible. This document records scope choices, methodology notes, and the upgrade paths that extend coverage.

## Cost numbers: methodology

`cost_delta_pct` is measured over `--holdout` inputs (default 2) × N=3 replays each, gated on a one-sigma signed band — AUTO_APPLY only when the cost win is stable across replays. Tier-2 mutations call an LLM rewriter that produces different specific SKILL.md / `helper.py` text on each invocation, so two consecutive `optimize` runs on the same corpus accept patches with the same shape and the same direction of cost win; the exact percentage shifts within the variance band. Raising `--holdout` tightens the estimate further — an operator knob.

## Equivalence is baseline-equivalence

The verifier compares the patched skill's output to the captured *baseline* output, byte-for-byte on declared `primary_fields`. `equivalence=1.00` means *"patched agrees with baseline"* — a conservative, auditable gate that gives reviewers a five-line code check (`_compare_primary_fields` in `verifier.py`) instead of an LLM-judge they have to trust. The spec accepts this trade because most skills don't ship labeled expected outputs.

**Two upgrade paths, both with codebase support already in place:**

- **Ground-truth where cheap.** `invoice_validator` ships an `expected_outputs/` directory alongside `sample_inputs/`. `scripts/d012_helper_groundtruth.py` runs the extracted `helper.py` against every input and compares the full output object — an independent ground-truth signal alongside the verifier's primary-field check. Other shapes (e.g. labeled routing for `ticket_router`) can ship the same way.
- **LLM-judge fallback gate.** Infrastructure is in place (`LLMClient` port + `ClaudeCliAdapter`); wiring into the equivalence path is the next extension — would let the verifier accept patches whose free-text fields agree semantically even when byte-strings diverge.

## D012 groups script artifacts by recurring function name

The detector groups Python `def`s by name. A function authored as `validate_invoice` in 45 runs but `validator` in 10 more counts as 45, not 55 — the rename hides the recurrence. A semantic upgrade (LLM judge: "are these the same function?") is future work, gated on a richer equivalence floor (same shape as D001's Layer-3 follow-on).

## D005 scope: byte-identical fields

DeterminismDetector compares output fields byte-for-byte across replays of the same input — fires on the deterministic structured-output skills it was built for. Free-text fields (`report_drafter`, `release_notes_compiler`) rephrase across replays, so D005 reports `partial` rather than `full` on those skills. The LLM-judge equivalence fallback above is the extension that recovers them.

The `step_determinize` `full` mode (every primary field stable corpus-wide → drop SKILL.md, emit `optimized.py` + `tests/` + `requirements.txt`) is the spec's stated endpoint. Reachable on skills where every primary field is stable (`invoice_validator` qualifies in principle); detector classification already supports it. Wiring the emission path is the next extension.

## D008 is named "pseudoparallelize" — real parallelism is future work

Cost wins from `pseudoparallelize_tools` come from the parallelization-oriented rewrite itself, not from concurrent execution at runtime. Pushing SKILL.md to emit parallel tool calls restructures the workflow into a tighter, more directive shape — the model uses fewer turns / fewer tokens to do the same work, and that's where the measured cost win comes from. The *pseudo-* prefix names exactly this: the parallelization-styled prompt is the mechanism, even though actual concurrent execution stays runtime-blocked.

**Verified:** across 10 SKILL.md prose variants (Haiku + Sonnet), Task-subagent dispatch, and the Anthropic-docs `<use_parallel_tool_calls>` block appended to Claude Code's full preset, `scripts/probe_parallelism.py` reports `avg_tools_per_message = 1.00` (docs say `> 1.0` if parallelism is working). `ClaudeAgentOptions` exposes no `parallel_tool_use` flag.

**Future work:** a custom MCP "batch tool" via `mcp_servers` that collapses N tool calls into one MCP call run with `asyncio.gather` — bypasses the model-emission constraint entirely. `Patch.new_files` already supports shipping MCP server source alongside the patched SKILL.md, so the architecture fits.

## Trace format: Claude Code SDK JSONL with an `output.json` Write

The optimizer reads Claude Code SDK JSONL (`assistant` / `user` records, `tool_use` content blocks). The skill's structured answer is recovered from the last `Write` whose `file_path` ends in `output.json` (or whatever filename the skill declared in its `output_path:` frontmatter). Both are conventions, not negotiable.

**Two layouts accepted:**

- **Captured** (`capture_traces.py` output): `<traces-root>/<skill>/baseline/{manifest.json, run_NNN.jsonl, run_NNN.output.json}`. Parallel `output.json` files are optional; the store extracts from JSONL when missing.
- **BYO:** drop `*.jsonl` into `<traces-root>/<skill>/baseline/`. No manifest, no parallel output files. Each trace = one run. Elapsed-time reporting unavailable (latency deltas show as zero).

**Not covered:** non-SDK traces (raw Anthropic API logs, OpenTelemetry spans, custom shapes) or skills whose output isn't routed through a `Write` tool call.

