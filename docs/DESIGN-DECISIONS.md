# Design Decisions

Locked decisions, the reasoning behind each, and where alternatives were rejected. ADR-style.

## ADR-001: SKILL.md frontmatter is the system's per-skill config home

**Decision.** Per-skill metadata the optimizer reads (`model`, `primary_fields`, `output_path`, `input_glob`, `summary_field`) lives in the skill's own `SKILL.md` YAML frontmatter. The optimizer treats `--skill` as a path; pointing it at any directory containing a `SKILL.md` and `sample_inputs/` is sufficient.

**Why.** The design brief frames the user model as *"a system that takes a skill and its execution traces."* A user should be able to run the optimizer on their own skill without editing source code. Hardcoding per-skill metadata in a `SKILL_REGISTRY` Python dict makes the registry a hidden coupling — any new skill needs source edits, contradicting the spec's "give it a skill" framing.

**Alternative rejected.** A central `SKILL_REGISTRY` keyed by skill name. Used during the early spike (it lowered friction for our own demo skills) but doesn't scale to arbitrary user-supplied skills. Subsequently removed — every per-skill knob (input glob, summary field, primary fields) now lives in SKILL.md frontmatter (with sensible defaults), and `--skill` is a path on every CLI.

## ADR-002: `primary_fields` resolution is three-tier

**Decision.** When the verifier needs to know which output keys count for the equivalence check, it resolves in this order:

1. **SKILL.md frontmatter** — `primary_fields: ["..."]` declared by the skill author.
2. **Auto-derive from baseline replay stability** — for each input that has ≥2 captured replays, find the keys whose values are byte-identical across those replays. Intersect across inputs. Use the result if non-empty.
3. **All top-level keys of the first baseline output** — final fallback.

Each tier is deterministic and auditable; the verifier records which tier produced the answer.

**Why three tiers.** Tier 1 alone makes the system unusable until authors learn the convention. Tier 3 alone (compare-all-keys) over-rejects when a skill emits a free-text field alongside structured ones — free text varies across replays and any patch fails. Tier 2 alone requires every user to have ≥2 replays per input — fine for our demo corpora, not necessarily for theirs. The tiers compose: author intent wins when present; otherwise infer what a sensible author would have written; otherwise err on the safe side.

**Why intersect across inputs in Tier 2.** A field stable for input A but variable for input B isn't a reliable equivalence signal. Intersection guarantees the chosen fields are stable across all evidence.

**Alternative rejected — LLM-judge inference of `primary_fields`.** Send SKILL.md to Opus, ask "which fields are load-bearing?", use the answer. Rejected on the runtime path: (a) non-determinism would make two `optimize` runs accept different patches; (b) *"Opus decided"* is a weaker REJECT trail than a frontmatter line; (c) authors already know their own fields. Reserved as future work for a `scaffold` subcommand that suggests a frontmatter line at setup time.

**Alternative rejected — hardcoded global heuristic.** Couples the verifier to a fixed schema. Unworkable across diverse skill shapes.

## ADR-003: Equivalence is strict-eq on declared primary fields, not semantic match

**Decision.** The verifier's equivalence check is Python `!=` on the declared (or resolved) primary fields. No tolerance band, no LLM judge in the equivalence path.

**Why.** Strict equality is the most auditable possible contract — anyone can verify the equivalence gate in five lines of code (`_compare_primary_fields` in `verifier.py`). Tolerance introduces configuration debt (per-field epsilon, absolute vs relative, numeric vs string) and masks regressions (a `helper.py` consistently 1¢ off due to wrong rounding direction passes tolerance but fails strict-eq).

**Alternative rejected.** LLM-judge equivalence as the primary check. Considered, planned as a *fallback* (cheap fast-pass strict-eq, second-chance LLM-judge on mismatch) but not implemented in this iteration. Infrastructure is in place (`LLMClient` port). See [`docs/LIMITATIONS.md`](LIMITATIONS.md).

**Knock-on.** Skills with free-text output fields cannot pass equivalence under strict-eq alone. Either the author declares `primary_fields` to exclude the free-text key, or the LLM-judge upgrade is needed. Documented in `LIMITATIONS.md`.

## ADR-004: Composition prunes verifier replay side-effects from the optimized output

**Decision.** `compose_optimized_skill` takes the original skill directory as a reference and prunes from the published optimized dir anything that wasn't in the original or in an AUTO_APPLY patch's `new_files`.

**Why.** Verifier replays run `claude` with `cwd=<staged_skill_dir>`. The model can `Write` arbitrary files into that working directory (and does, for skills whose pre-patch SKILL.md instructs script authoring — e.g. `invoice_validator` and `loan_calculator` before D012 lands). Without pruning, those files accumulate in the staged dir and get copied through to `runs/<id>/optimized/<skill>/` — opening the optimized dir would show five throwaway `.py` files alongside `helper.py`, with no way to tell which is which.

**Alternative rejected.** Per-replay tempdir (verifier replays into a fresh copy of the staged dir, throwaway). Cleaner conceptually but a larger change to `verifier.py`; the prune is localized to one function and equivalent in effect.

## ADR-005: D012 groups script artifacts by recurring function name, not semantic equivalence

**Decision.** D012's detector groups Python `def`s by name. A function authored across ≥`d012_min_occurrences` distinct runs (default 3) constitutes a re-derived script.

**Why.** Recurring function names are a high-signal cheap heuristic for the script-re-derivation pattern in skills like this — the model picks different filenames every run (`validate.py` / `validator.py` / etc.) but the core function name (`validate_invoice`) is the constant across them. The heuristic catches the spec's category 1 case (*"stop re-deriving the same code"*) without requiring an LLM judge in the detector path.

**Alternative rejected.** LLM-judge "are these the same script semantically" upgrade. Would catch renamed-function cases (model writes `validate` in one run, `check_invoice` in another). Documented as future work; mirrors D001's Layer-3 LLM-judge follow-on, gated on richer equivalence machinery (`LIMITATIONS.md`).

## ADR-006: The capture pipeline runs each invocation in a tempdir copy

**Decision.** `scripts/capture_traces.py` copies the skill directory to a per-run tempfile.TemporaryDirectory before invoking `claude`. The model authors files into that tempdir; the source `demo/skills/<skill>/` is read once and never written to.

**Why.** Without this, the model's `Write` tool calls during capture pollute the source skill directory with throwaway scripts (and an `output.json`). Once those files are committed (or even just exist locally), they leak into the optimized output via the composition copy. The tempdir gives capture the same property the verifier already had: the skill's source-tree representation is the *spec* of the skill, not the *result of running* it.

**Alternative rejected.** `.gitignore` patterns for known throwaway script names. Fragile (the model picks different filenames each run) and doesn't help on a fresh checkout where the files don't exist yet but get written on the next capture.

## ADR-007: Tier-2 mutation rewriters share LLM-response validation via `_rewriter_io`

**Decision.** The two helper functions (`strip_preamble_to_frontmatter`, `is_plausible_skill_md`) used by every Tier-2 LLM rewriter live in one module — `domain/mutations/_rewriter_io.py` — and are imported by each rewriter.

**Why.** They were independently re-implemented in 5 mutation files with slight drift (one used `len < 80`, others `len < 100`). One source of truth; consistent acceptance criteria across all Tier-2 mutations; one place to upgrade the validator if the LLM's response shape changes.

## ADR-008: Deterministic pipeline + LLM-as-primitive, not an agent loop

**Decision.** The optimizer is a **deterministic pipeline** with LLM calls as primitive operations, not an agent architecture (no ReAct, Reflexion, Plan-and-Execute, ReWOO, Tree-of-Thoughts, supervisor-worker). The flow:

```
Trace → [detector: pure code] → Finding
Finding → [mutation: 0 or 1 LLM call] → Proposal
Proposal → [verifier: pure code; runs patched skill via Claude SDK] → VerificationResult
VerificationResult → [decision policy: pure code, threshold gate] → AUTO_APPLY / FLAG / REJECT
```

The agent loop (ReAct) does exist — *inside the skill under test*. Claude Code SDK running a SKILL.md is itself an agent. The optimizer *around* that agent is non-agentic by design: linear dispatch, one LLM call per Tier-2 mutation, no inter-step LLM reasoning.

**Why deterministic for this domain.**

1. **Auditability.** The spec requires decisions *"easily auditable by humans."* A deterministic gate is a five-line code audit (`_compare_primary_fields` + `decide()`); *"cost delta met threshold and equivalence was 1.00 on `valid`"* is a much stronger trail than *"the critic LLM thought the outputs were equivalent."*
2. **Cost.** End-to-end on `invoice_validator`: 3 LLM calls in our system vs. 45+ in an agentic equivalent (ReAct: N iterations × K sub-calls). This is a cost optimizer — multiplying tokens to build it is the wrong shape.
3. **Determinism is the contract.** Strict-eq on declared primary fields is the cleanest non-regression check. Moving the gate to *"Opus thinks these are equivalent"* turns a hard contract into a soft judgment made by the same model family being optimized — recursive trust.

**Where agent patterns belong.** On the fallback / upgrade path, not the hot path. Specifically: LLM-judge equivalence as a Reflexion-style second-chance gate when strict-eq fails (infrastructure in `LLMClient` port; wiring is future work in `LIMITATIONS.md`); ReAct-style mutation retry when a Tier-2 rewrite produces an implausible response; cross-detector reasoning as a supervisor layer (today detectors are independent).

**Alternative rejected.** Full LangGraph / LangChain agent flow with a supervisor agent dispatching to detector-specialist sub-agents. Fails the audit requirement; costs ~10× more in tokens.

## ADR-009: D008 is named `pseudoparallelize`, real parallelism is future work

**Decision.** The Tier-2 mutation that rewrites SKILL.md to instruct parallel tool execution is named `pseudoparallelize_tools` (not `parallelize_tools`). The detector pairs as `D008 PseudoparallelizationDetector`. `decisions.jsonl` records the mutation as `pseudoparallelize_tools`, not making a claim the runtime can't fulfill.

**Why.** Experiments tested every prompt-level lever: 10 SKILL.md prose variants (Haiku + Sonnet), Task-subagent dispatch, Claude-UI-framed directives, and the Anthropic-docs `<use_parallel_tool_calls>` system-prompt block appended to Claude Code's full preset. Across every configuration, `scripts/probe_parallelism.py` reports `avg_tools_per_message = 1.00` (docs say `> 1.0` if parallelism is working). `ClaudeAgentOptions` exposes no `parallel_tool_use` flag, and the Claude Code non-interactive runtime does not unlock concurrent `tool_use` emission regardless of system-prompt strength.

So the cost wins the mutation produces come from the parallelization-oriented rewrite itself: pushing the SKILL.md to emit parallel tool calls restructures the workflow into a tighter, more directive shape, and the model uses fewer turns / fewer tokens. The *pseudo-* prefix names this — the parallelization-styled prompt is the mechanism, not actual concurrent execution. Full evidence + future-work options in `LIMITATIONS.md` Section "D008 pseudoparallelize."

**Related fix.** `runtime.py` was passing a plain-string `system_prompt` to `ClaudeAgentOptions`, which silently overrode Claude Code's full default system prompt. Switched to the preset+append form: `{"type": "preset", "preset": "claude_code", "append": <cwd anchor + the parallel directive>}`. The Anthropic-docs parallel directive is kept in the append even though the probe showed it doesn't unlock parallelism — the directive's presence is the audit trail of what was attempted.

**Alternative rejected.** Drop D008 entirely. The parallelization-oriented rewrite produces a real cost win that gates through the verifier like any other mutation; the right answer is the *pseudo-* rename, not deletion.

**Future work.** Custom MCP "batch tool" via `mcp_servers` that collapses N tool calls into one MCP call run with `asyncio.gather` — bypasses the model-emission constraint entirely. `Patch.new_files` already supports shipping the MCP server source alongside the patched SKILL.md, so the architecture fits.

## ADR-010: `confidence` field removed from `OptimizationDecision`

**Decision.** The `confidence` field was removed from `OptimizationDecision`. `human_rationale` is the per-decision explanation.

**Why.** The field was a hardcoded constant per verdict (0.92 AUTO_APPLY / 0.60 FLAG / 0.85 REJECT). Carried no per-decision information. The `human_rationale` string already states the gate math (cost delta, equivalence ratio, replay variance); a constant float alongside it was audit-trail noise.

**Alternative deferred.** A real confidence score derived from variance bands, threshold-distance, or detector reliability priors. Future work, not a hardcoded constant.
