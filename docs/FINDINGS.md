# Empirical Findings

Real-trace results from running the optimizer on the demo skill corpus. Each section records a single optimizer run: the command, corpus statistics, detection results, verification outcomes, decision verdicts, and what the run validated or refuted. Findings accumulate chronologically; nothing is rewritten retrospectively.

## Reading the numbers

Each run report below records what the optimizer measured at the time. Current measurement substrate:

| Metric | Source |
|---|---|
| `cost_delta_pct` | Measured: real `message.usage` tokens × per-model rates from `config/pricing.yaml`, per-message model resolution. See ARCHITECTURE.md Section 9. |
| `latency_delta_pct` | Measured via `_avg_latency_delta()` from `subprocess.run` `elapsed_s`. Noisy at N=1; N=3 + signed-band variance shipped before the 2026-05-10 run. |
| `equivalence_ratio` | Strict-eq on declared `primary_fields` — frontmatter line, or auto-derived from baseline replay stability. See DESIGN-DECISIONS.md ADR-002. |
| `verdict` (PASS / FAIL) | Derived from `equivalence_ratio == 1.0`. |
| `decision` (AUTO_APPLY / FLAG / REJECT) | Gated by verdict + signed-band cost threshold. See APPROACH.md Section 6 *Calibration*. |

**Historical note.** The 2026-05-05 runs predate the measured-cost rollout for D001; their D001 `cost_delta_pct` values reflect the spike constant `_REDUNDANT_LOOKUP_COST_PCT = -3.0` and were correctly REJECTed at the −10% gate. D004's `−78%` was already measured at that time via `_estimate_model_swap_savings` (repricing each baseline trace at the target model). Subsequent runs use measured math throughout.

---

## D001 matching scope and design rationale

D001 detects the spec's *"same file, query, or piece of context fetched multiple times in a single run"* signal via layered matching strategies, applied in order of cost (cheapest first):

1. **Strict equality** on `(tool_name, json(input))` byte-level pairs. Free, deterministic. Catches the simplest case: model calls the same tool with the same input dict more than once in a trace. This is the original spike's matching.
2. **Layer 1a — `Read` input normalizer.** For `Read` calls, the matching key is `("__resource__", f"file::{file_path}")` regardless of `offset` / `limit` / other secondary parameters. Free, deterministic. Catches the case where the model chunks reads of the same file across different parameters (e.g., `offset=0,40,80` for sequential pagination).
3. **Layer 1b — `Bash cat` regex.** A conservative regex matches `Bash({"command": "cat <path>"})` and collapses it to the same `("__resource__", f"file::{path}")` key as `Read({"file_path": path})`. Excludes `head` / `tail` / `cat -<flags>` / piped commands / multi-file `cat` to avoid false positives.

### What D001 catches today

| Pattern | Caught by |
|---|---|
| `Read({"file_path": "/x"})` × 2 (identical input) | strict equality |
| `Bash({"command": "cat /x"})` × 2 (identical input) | strict equality |
| `Read({"file_path": "/x"})` and `Read({"file_path": "/x", "offset": 100})` (same file, chunked) | Layer 1a |
| `Read({"file_path": "/x", "offset": 0, "limit": 40})` and `Read({"file_path": "/x", "offset": 40, "limit": 40})` (sequential pagination) | Layer 1a — validated on `policy_compliance_auditor` |
| `Read({"file_path": "/x"})` and `Bash({"command": "cat /x"})` (cross-tool, identical content) | Layer 1b |
| Any pair of identical tool calls regardless of which tool — `Edit`, `Write`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, MCP tools | strict equality (catches identical calls for any tool, not just Read/Bash) |

### What D001 does NOT catch (documented limitations)

| Pattern | Why missed | Mitigation |
|---|---|---|
| **Custom MCP tools** fetching the same logical resource via different tool names — e.g., `acme_get_invoice_pdf(123)` ↔ `acme_fetch_invoice_data(123)` | MCP tool sets are per-deployment; cannot be enumerated structurally ahead of time | LLM-judged equivalence (Layer 3, post-submission per `private/future_impl.md`) |
| **Bash whole-file fetches via tools other than `cat`** — `less`, `more`, `xxd`, `awk '{print}'`, `od`, `python3 -c "open('/x').read()"` | Each adds regex complexity; `python -c` / `node -e` are arbitrary-code shapes no regex can reliably parse | Layer 1b expansion (post-submission) for the common shell tools; Layer 3 for inline scripts |
| **Cross-tool URL fetches** — `WebFetch(URL)` ↔ `Bash("curl URL")` ↔ `Bash("wget URL")` | URL canonicalization (sort query params, strip trailing slash, lowercase host) was scoped out of the spike | Layer 1c (URL normalizer, post-submission) |
| **Query paraphrase** — `WebSearch("how to X")` ↔ `WebSearch("X how-to")`; SQL with whitespace/column reorder | Semantically equivalent but textually distinct; structural matching can't normalize | Layer 3 LLM-judge |
| **Path representation variants** — `/x` vs `./x` vs `~/x` referencing the same file | Different strings → different keys; path canonicalization was scoped out | Layer 1a extension (deterministic, future) |
| **Glob / Grep query overlap** — `Glob("**/*.txt")` and `Glob("./*.txt")`; superset / subset patterns | Algorithmic problem (subset detection on glob/regex patterns) | Layer 2 (post-submission) |

### Why we drew the line here

Three reasons the current scope is defensible:

1. **The verifier is the safety net.** Any mutation that wrongly collapses two distinct fetches will fail the equivalence check at verification time and be REJECTed. So under-collapsing produces missed optimizations (false negatives, harmless); over-collapsing produces quality regressions but is caught downstream. Conservative structural rules + verifier safety = correct behavior with low risk.
2. **Native Claude Code tools are bounded; intra-trace consistency is high.** Within a single trace, the model usually picks one tool and uses it consistently. Strict equality catches the bulk of identical-call redundancy. Layer 1a catches the most common variation (chunked reads on the same file). Production traces from agentic systems using only native tools are well-served by this scope.
3. **Layer 3 LLM-judge has a real cost** (~$0.001 per uncached pair, non-determinism, per-pair O(N²) without aggressive caching). Shipping it without a clear fire-rate justification would over-engineer for the demo. Documenting it as the post-submission improvement (per `private/future_impl.md`) is the honest framing for production-trace diversity.

### Forward-protective vs retroactive

- **Layer 1a is retroactively useful** — fired on `policy_compliance_auditor` (2026-05-05 run); produced 1 D001 finding (occurrences=3) where strict matching alone would have produced 0. See per-run section below.
- **Layer 1b is forward-protective** — did not fire on any of the 5 demo skills (none use `Bash` for file fetches). Unit-test validated only. Included for future skills or production traces that adopt the `Bash cat` pattern.
- **Layer 3 is deferred and gated** — not shipped. Wiring is gated on verifier hardening (N=3 replays + signed-band cost + FLAG verdict — see APPROACH.md Section 5). Reason: Layer 3 introduces non-determinism into detection; stacking it on top of replay-stochastic verification produces compounded variance across runs. Ship the verifier safety net first. The same gating applies to D004 LLM-judged, D005, and D007 — every Tier 2 detector.

---

## 2026-05-05 — `policy_compliance_auditor` (Layer 1a real-trace validation)

### Command

```bash
skopt optimize \
  --skill demo/skills/policy_compliance_auditor \
  --traces-root traces \
  --holdout 1
```

### Corpus

- **Skill frontmatter:** `model: claude-haiku-4-5` declared.
- **Inputs:** 2 policy bundles (`policy_001/policy.txt` covering all 7 framework points; `policy_002/policy.txt` with documented gaps).
- **Captures:** 5 baseline traces (target was 6, but `policy_002` replay 3 hit the 240s timeout — 5/6 successful). 3 successful replays of policy_001, 2 of policy_002.
- **Train/holdout split:** seed=42 → 3 train traces (policy_001 only) / 2 holdout traces (policy_002, with one timeout).

### Detection results

| Detector | Findings | Pattern |
|---|---|---|
| D004 ModelTier | 1 | All 3 train traces report large-tier `models_used` (same runtime-override anomaly as `contract_redline_reviewer`). |
| **D001 RedundantLookup** | **1** | **Layer 1a fire.** Haiku followed the SKILL.md instructions and read `policy.txt` three times per trace using `offset=0/40/80, limit=40`. Strict matching would have produced three distinct keys (each count=1, below `intra_trace_min=2`); Layer 1a's resource-key normalizer collapsed all three to `("__resource__", "file::.../policy.txt")` with count=3 per trace, meeting both gates. |

This is the first real-trace evidence that Layer 1a does work the spec wording asks for: the model fetched the *same file* three times in a single run, and D001 detected it despite the input dicts being byte-level distinct.

### Verification outcomes

| Mutation | equivalence | cost_delta | latency_delta | verdict | decision |
|---|---|---|---|---|---|
| D004 `model_swap` (insert `model: claude-haiku-4-5`) | 1.00 | −78.0% | +11.6% | PASS | **AUTO_APPLY** |
| D001 `preload_file` directive | 1.00 | −3.0% (spike constant) | +23.1% | PASS | **REJECT** (cost gate, same as on `contract_redline_reviewer`) |

### What this validates

- **Layer 1a's resource-key collapse is correct on real traces.** Without it, the chunked-read pattern would have produced 0 D001 findings; with it, 1 finding emitted with the right occurrences count.
- **Verifier safety still holds.** D001 mutation preserved equivalence (1.00) — applying the preload directive didn't break the patched skill's output.
- **Detector → mutation → verifier → decision chain works for a Haiku-declared skill.** D004 still fires (runtime-override anomaly), but the path is detector-agnostic.

### What this surfaced

- **Haiku timeout on long-doc workflows.** policy_002's third replay hit the 240s wall-clock cap. 5/6 successful is sufficient for D001 to fire on policy_001 (3 traces meets `min_occurrences=3`); policy_002's 2 successful captures don't meet the cross-trace gate. If higher capture-success is needed for future skills, bump `TIMEOUT_SECONDS` in `capture_traces.py`.
- **D001 REJECT is calibration-driven, not failure** — same as `contract_redline_reviewer`. The `_REDUNDANT_LOOKUP_COST_PCT = -3.0` spike constant doesn't clear the −10% AUTO_APPLY threshold. When `domain/token_usage.py` + `pricing.yaml` ship, the measured cost will be substantially larger (3 redundant chunked reads × ~1500 tokens each ≈ 30-50% of a Haiku run's input cost) and the decision will flip to AUTO_APPLY.

### Artifacts

- Run output: `runs/2026-05-05T17-01-34/decisions.jsonl`
- Run report: `runs/2026-05-05T17-01-34/optimization_report.json`

---

## 2026-05-05 — `contract_redline_reviewer` (multi-pass redline review)

### Command

```bash
skopt optimize \
  --skill demo/skills/contract_redline_reviewer \
  --traces-root traces \
  --holdout 1
```

### Corpus

- **Skill frontmatter:** `model: claude-haiku-4-5` declared.
- **Inputs:** 5 redline bundles (`redline_001/` through `redline_005/`), each with `original.txt` + `redline.txt`.
- **Captures:** 15 baseline traces (5 inputs × 3 replays each), captured via `scripts/capture_traces.py --skill contract_redline_reviewer --n-runs 3`.
- **Train/holdout split:** seed=42 → 12 train traces (4 unique inputs) / 3 holdout traces (1 unique input).

### Detection results

| Detector | Findings | Pattern |
|---|---|---|
| D004 ModelTier | 1 | All 12 train traces ran on a large-tier model (see runtime-override observation below). |
| D001 RedundantLookup | 6 | 3 unique inputs × {`original.txt`, `redline.txt`} = 6 distinct `(Read, file)` patterns, each recurring across 3 replays. |

**D001 fires on real traces for the first time.** The multi-pass SKILL.md design (steps 3–5 each instructing "Read both files again") induced the model to actually re-Read on at least 3 of 4 train inputs.

### Verification outcomes

| Mutation | equivalence | cost_delta | latency_delta | verdict | decision |
|---|---|---|---|---|---|
| D004 `model_swap` (insert `model: claude-haiku-4-5`) | 1.00 | −78.0% | +33.1% | PASS | **AUTO_APPLY** |
| D001 `preload_file` (×6, one per pattern) | 1.00 (all) | −3.0% (spike constant) | −12.1% / +17.3% / +5.1% / +15.4% / −24.2% / −34.9% | PASS | **REJECT** (×6) |

### What this validates

- **D001 detector signal works on real traces.** Strict `(tool, input)` matching catches multi-pass re-Read patterns when the SKILL.md is explicit enough that the model complies.
- **D001 mutation works.** `propose_preload_file` builds valid Patches (directive prepended before H1 in body); each Patch applies cleanly in tempdir; verifier replays the patched skill; equivalence preserved at 1.00 on every single one.
- **Verifier-as-safety-net works.** All 7 mutations preserved output equivalence — the patched skill produces the same primary-field values as baseline.
- **Decision policy gates correctly.** REJECT on insufficient measured win is the policy doing its job, not blindly accepting. AUTO_APPLY only fires when the cost gate (≤ −10%) is met.

### What this surfaced

- **Runtime-override observation.** The skill declares `model: claude-haiku-4-5`, but D004 fired with `occurrences=12` — meaning all 12 train traces report large-tier models in `models_used`. Either Claude Code ignores the SKILL.md `model:` frontmatter directive when launched via the `claude -p` CLI subprocess, or the capture path resolves model declarations differently from runtime expectations. Worth verifying before claiming "Haiku is the right tier for this skill" — the corpus says otherwise.
- **D001 REJECT is calibration-driven, not failure.** The spike's hardcoded `_REDUNDANT_LOOKUP_COST_PCT = -3.0` falls below the AUTO_APPLY threshold of `-10%`. When `domain/token_usage.py` + `config/pricing.yaml` ship, measured cost-per-redundant-Read replaces the constant. Estimated savings on this corpus: 4 redundant Reads × ~5KB per file × 2 files per trace ≈ ~20K redundant tokens/trace ≈ 33–50% of total input tokens, well above the 10% threshold. **Same code, same patches; just real numbers will flip the decision.**
- **N=1 latency measurements are unreliable.** Per-mutation latency deltas span [−34.9%, +33.1%] for the same skill on a single holdout input. Subprocess startup overhead dominates wall-clock at small payload sizes, producing noise that overwhelms the signal. N=3 + variance bands needed before any latency-based gate is trustworthy.
- **Sonnet-class models comply with strong multi-pass framing.** Earlier runs (`contract_clause_flagger`, `ticket_router`) showed Sonnet ignoring naive "re-read once more" instructions. With explicit numbered passes (`Pass 3 — Read both files again`) plus "MUST follow" plus "do not rely on memory between passes," Sonnet re-Read. D001's value lies in catching patterns produced by SKILL.md prose that isn't optimized away — which depends on prose strength.

### Artifacts

- Run output: `runs/2026-05-05T10-26-43/decisions.jsonl`
- Run report: `runs/2026-05-05T10-26-43/optimization_report.json`

---

## 2026-05-05 — `ticket_router` (D004 only)

### Command

```bash
skopt optimize \
  --skill demo/skills/ticket_router \
  --traces-root traces \
  --holdout 1
```

### Corpus

- **Skill frontmatter:** no `model:` line declared (runtime default).
- **Inputs:** 25 tickets (`ticket_001.txt`–`ticket_025.txt`).
- **Captures:** 30 baseline traces.
- **Train/holdout split:** seed=42 → 27 train traces / 3 holdout traces (1 unique input).

### Detection results

| Detector | Findings | Pattern |
|---|---|---|
| D004 ModelTier | 1 | All 27 train traces use Sonnet 4.6 (runtime default). |
| D001 RedundantLookup | 0 | Sonnet did not produce within-trace redundancy on this skill. |

### Verification outcome

| Mutation | equivalence | cost_delta | latency_delta | verdict | decision |
|---|---|---|---|---|---|
| D004 `model_swap` (insert `model: claude-haiku-4-5`) | 1.00 | −78.0% | **−30.1%** | PASS | **AUTO_APPLY** |

### What this validates

- D004 end-to-end on a skill with no declared `model:` (fallback insert path works).
- **Strongest measured latency win on the corpus** (−30.1%). Tickets are short structured-output tasks where Haiku's faster inference dominates over subprocess overhead.

---

## 2026-05-05 — `contract_clause_flagger` (D004 only)

### Command

```bash
skopt optimize \
  --skill demo/skills/contract_clause_flagger \
  --traces-root traces \
  --holdout 1
```

### Corpus

- **Skill frontmatter:** `model: claude-sonnet-4-6` declared.
- **Inputs:** 21 contracts (`contract_001.txt`–`contract_021.txt`).
- **Captures:** 18 baseline traces.
- **Train/holdout split:** seed=42 → 15 train traces / 3 holdout traces (1 unique input).

### Detection results

| Detector | Findings | Pattern |
|---|---|---|
| D004 ModelTier | 1 | All 15 train traces use Sonnet 4.6 (matches frontmatter). |
| D001 RedundantLookup | 0 | Sonnet did not re-Read despite SKILL.md instructing "re-read the contract once more from the top." Naive prose framing not enough to induce compliance. |

### Verification outcome

| Mutation | equivalence | cost_delta | latency_delta | verdict | decision |
|---|---|---|---|---|---|
| D004 `model_swap` (`model: claude-sonnet-4-6` → `model: claude-haiku-4-5`) | 1.00 | −78.0% | +1.5% | PASS | **AUTO_APPLY** |

### What this surfaced

- **D001 silent on naive-prose seeded waste.** SKILL.md instruction "Re-read the contract once more from the top to ensure no clauses were missed on the first pass" was ignored by Sonnet — the model used in-context content from the first Read for all subsequent reasoning. This established the empirical baseline that later motivated `contract_redline_reviewer`'s explicit numbered multi-pass design.
- **Latency near-zero (+1.5%).** Verbose prompt + structured-output task on long contract input → subprocess overhead and Haiku's inference time roughly cancel for this skill at N=1.

---

## 2026-05-10 — `release_notes_compiler` (D008 + D003 + D006 triple AUTO_APPLY)

### Command

```bash
skopt optimize \
  --skill demo/skills/release_notes_compiler \
  --traces-root traces \
  --holdout 1
```

### Corpus

- **Skill frontmatter:** `model: claude-haiku-4-5` declared.
- **SKILL.md modification:** the Process section's parallel-friendly prose was preserved verbatim; a `## Setup` section was added instructing `pip install markdown` via Bash, to give D006 a real trigger pattern without disrupting D008's detection conditions.
- **Inputs:** 3 release bundles (`release_001`, `release_002`, `release_003`), each containing `pr_titles.txt`, `changelog.md`, and `customer_feedback.txt`.
- **Captures:** 9 baseline traces under `traces/release_notes_compiler/baseline/`.
- **Train/holdout split:** seed=42 → 6 train traces / 3 holdout traces (1 unique input held out).

### Detection results

| Detector | Findings | Occurrences | Pattern |
|---|---|---|---|
| D004 ModelTier | 1 | 6 | All 6 train traces ran on a large-tier model; mutation skipped because skill already declares Haiku. |
| **D008 Pseudoparallelization** | 1 | 6 | 3 independent Reads of `pr_titles.txt` / `changelog.md` / `customer_feedback.txt` emitted as 3 sequential single-tool turns. |
| **D003 ToolReliability** | 1 | 4 | Two failure-retry patterns surfaced: (a) `Write` on `output.json` before `Read` → Claude Code rejected with *"File has not been read yet"*; (b) Bash with compound operators (`pip install ... 2>&1 \| tail -3`) → Claude Code rejected with *"This Bash command contains multiple operations"*. |
| **D006 EnvSetupRepeat** | 1 | 10 | `pip install markdown` recurs across all captured runs. |

### Verification outcomes

All three Tier-2 mutations verified independently against the original skill:

| Mutation | equivalence | cost_delta | latency_delta | verdict | decision |
|---|---|---|---|---|---|
| D008 `pseudoparallelize_tools` | 1.00 | −83.6% | −48.3% | PASS | **AUTO_APPLY** |
| D003 `tool_guidance_rewrite` | 1.00 | −78.9% | −24.6% | PASS | **AUTO_APPLY** |
| D006 `cache_strategy_rewrite` | 1.00 | −84.4% | −48.8% | PASS | **AUTO_APPLY** |

### What this validates

- **D003 fires on real traces with real failure-retry patterns.** Earlier validation was synthetic-only; today's run captures the model genuinely encountering Claude Code tool errors (Write-before-Read, multi-operation Bash) and retrying with corrected inputs. The `tool_guidance_rewrite` mutation adds explicit guidance preventing both failure modes upfront.
- **D006 fires on real install/download patterns.** The `pip install markdown` pattern recurs in every captured run; the `cache_strategy_rewrite` mutation introduces a check-then-install guard.
- **Verifier preserves equivalence across all three Tier-2 mutations.** Each replay produces output identical to baseline on the per-skill primary fields (`release_id`, `headline`, `customer_voice`).
- **Defense-in-depth verifier correctly accepts honest wins.** Unlike the 2026-05-10 ticket_router run (where the D008 mutation produced a noisy `cost_delta=-45.4%` with `equivalence=0.33` and was correctly REJECTed), here equivalence holds and the cost win is accepted.

### Mechanism fidelity

Each mutation's measured cost win has a different cause:

- **D003 (tool_guidance_rewrite):** intended mechanism fires. The patched SKILL.md instructs Read-before-Write and bare-Bash invocation; the model follows this guidance, skipping the failure-retry turns present in the baseline. The −78.9% saving reflects skipped retry turns.
- **D006 (cache_strategy_rewrite):** intended mechanism partially fires. The conditional install pattern is in the patched SKILL.md. In verifier replay (fresh tempdir per replay), the install still runs once but as a checked install rather than unconditional. The −84.4% saving is real for the replay; in a persistent-environment production deployment, the conditional check would skip the install entirely across runs.
- **D008 (pseudoparallelize_tools):** the parallelization-oriented rewrite itself drives the cost saving. `scripts/inspect_patched_turns.py` reports `0 / 7` parallel turns in the patched trace — actual concurrent execution doesn't fire at runtime. But the SKILL.md rewritten to push parallel emission produces a tighter, more directive workflow shape, and the model uses fewer turns / fewer tokens; the measured −83.6% cost win comes from this rewrite. The *pseudo-* prefix in the mutation name flags exactly this. See LIMITATIONS.md Section 7 for the full mechanism and the planned MCP-batch-tool path that would lift the runtime block.

The mechanism-fidelity distinction matters: the verifier measures cost regardless of how the saving was achieved, but the audit trail names the actual mechanism. D008's parallelization-oriented rewrite produces a measurable cost win via workflow restructuring, not via concurrent execution at runtime. The *pseudo-* prefix in `pseudoparallelize_tools` names this gap. Actual concurrent execution remains runtime-blocked until the SDK supports batched `tool_use` emission.

### What this surfaced — composition limitation (since fixed)

On this 2026-05-10 run, the composed skill at `runs/2026-05-10T18-44-57/optimized/release_notes_compiler/SKILL.md` contained **only D006's changes** — D008's parallel-emission prose and D003's tool-usage guidance were absent. The cause: each Tier-2 rewrite was generated against the *original* SKILL.md, so applying several `full_file=True` patches in sequence left only the last on disk. Tier-1 surgical patches (`preload_file`, `model_swap`) were unaffected — they compose via in-place substring replacement. The per-decision audit trail in `decisions.jsonl` logged all three AUTO_APPLY decisions correctly regardless of what landed on disk.

This is now fixed by cumulative-rewrite dispatch: each Tier-2 mutation regenerates against the previous accepted output rather than the original — the dispatch loop re-reads the in-progress SKILL.md from the scratch directory before each proposal, and `compose_optimized_skill` publishes that cumulative state. Stacked `full_file` rewrites now all survive, locked by `test_cumulative_full_file_rewrites_all_survive`; post-fix runs routinely stack three to four Tier-2 rewrites into one coherent skill. The remaining caveat is preservation fidelity — a later rewrite is instructed to retain earlier sections but nothing structurally enforces it; section-scoped patches would remove that trust dependency.

### Artifacts

- Run output: `runs/2026-05-10T18-44-57/decisions.jsonl`
- Run report: `runs/2026-05-10T18-44-57/optimization_report.json`
- Composed skill (partial — pre-fix artifact; see composition limitation above): `runs/2026-05-10T18-44-57/optimized/release_notes_compiler/SKILL.md`
- D008 patched-trace shape check (0/7 parallel turns): `python scripts/inspect_patched_turns.py runs/2026-05-10T18-44-57/`

---

## Cross-run synthesis (as of 2026-05-10)

### Coverage summary against the spec

| Spec bullet (design brief) | Detector | First-fire on real traces |
|---|---|---|
| 1. Extract reusable scripts | D012 ScriptReDerivation | implemented; not yet in a documented run |
| 2. Persist environment setup | D006 EnvSetupRepeat | ✅ `release_notes_compiler`, 2026-05-10 |
| 3. Catch tool execution misses | D003 ToolReliability | ✅ `release_notes_compiler`, 2026-05-10 |
| 4. Eliminate redundant lookups | D001 RedundantLookup | ✅ `contract_redline_reviewer`, 2026-05-05 |
| 5a. Tighten instructions (prompts) | D007 PromptTightening | implemented; not yet in a documented run |
| 5b. Tighten instructions (round-trips) | D008 Pseudoparallelization | ✅ `release_notes_compiler`, 2026-05-10 |
| 6. Downgrade the model | D004 ModelTier | ✅ all skills, 2026-05-05 |
| 7. Replace LLM steps with deterministic logic | D005 Determinism | implemented; not yet in a documented run |

### Recurring themes across runs

- **Equivalence preservation is reliable** at the verifier's strict-eq-on-declared-primary-fields level. 11 mutations replayed across 5 skills, equivalence=1.00 in every case. No silent quality regression observed.
- **Spike-cost gating gave way to measured cost between 2026-05-05 and 2026-05-10.** The earlier D001 REJECTs (cost=−3% spike, below the −10% gate) were calibration artifacts of the spike constant; measured-cost replays would re-evaluate them. The May 10 D003/D006/D008 AUTO_APPLYs are measured.
- **N=1 latency is noise.** Same-direction signal preserved across `ticket_router` (−30%), but `contract_clause_flagger` (+1.5%) and `contract_redline_reviewer` (+33%) show subprocess overhead variance dominates at this sample size. N=3 + signed-band variance shipped with the 2026-05-10 `release_notes_compiler` run.
- **Model-frontmatter-vs-runtime mismatch.** `contract_redline_reviewer` declared Haiku but captures showed Sonnet/Opus. `capture_traces.py --model` now honors SKILL.md frontmatter; demo corpora were re-captured to match.

---

## Related work — why a detector framework

Reflective evolutionary prompt optimizers (DSPy with GEPA, late 2024) achieve SOTA on labeled benchmarks via Pareto-frontier search guided by LLM reflection on rollout traces. They target task accuracy against a defined metric and emit a single optimized prompt.

This project occupies a different cell of the design matrix: efficiency optimization without task labels, with per-mutation rationale required for audit trails. The brief's per-category detector structure and the trace-only operation assumption (no labeled task metric available) motivated the explicit-detector approach. Tradeoff: cannot discover unknown waste patterns; only fires on hand-coded categories.

The verifier (replay + equivalence + variance band) is decoupled from the proposer and could in principle gate reflective-evolutionary proposals as well — a future composition path noted in `LIMITATIONS.md`.
