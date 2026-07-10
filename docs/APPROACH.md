# Approach

*This document captures the methodology, scope, and starting hypotheses for this project. It is a living document: empirical results, calibration tuning outcomes, and detector-by-detector findings accumulate in [FINDINGS.md](./FINDINGS.md) and [EVALUATION.md](./EVALUATION.md) as runs complete.*

---

## 1. The question being answered

A Claude Code skill is *wasteful* when it spends more cost, latency, model tier, or tool calls than its output requires — and *correct* when the cheaper version produces the same output the original would have. This project asks whether both properties can be measured mechanically against a skill's captured execution history.

Concretely: given a `SKILL.md` and N execution traces, can the optimizer detect waste patterns, propose minimal mutations, replay both versions on held-out inputs, and accept only mutations that preserve output equivalence with full evidence at every step.

This is *not* a generative problem (like writing better skills from scratch). It's an *analysis-and-rewriting* problem: existing skills, execution history as evidence, regression-free equivalence as the acceptance bar.

---

## 2. Scope

The bounds below are deliberate scope decisions, not unaddressed limitations. They keep the build tractable and let readers assess work-per-decision rather than coverage breadth.

**Skill shape.** This project targets single-purpose Claude Code skills with a bounded tool surface. The harness passes a fixed allowlist (`Read`, `Edit`, `Write`, `Bash`) to the SDK; per-skill `allowed_tools` enforcement via SKILL.md frontmatter is documented future work. The eight demo skills under `demo/skills/` cover the target platform's named workflow areas (invoices, contracts, tickets, reports) and adjacent structured-output cases. 

*Out:* multi-agent orchestration skills, deeply-nested LLM invocations.

**Waste taxonomy.** The optimizer covers the named waste categories in the original design brief plus a small number of additional patterns identified during architecture work. 

*Out:* waste patterns outside this taxonomy. Adding categories is an extension point, not an MVP commitment.

**Mutations.** No exploratory loop — LLM-driven rewriters get one attempt; verifier rejection → REJECT. Bounded refinement (one retry with the failure reason) is documented future work. 

*Out:* unbounded ReAct loops, evolutionary search across mutation populations, supervisor-led mutation orchestration.

**Out of scope (deliberate).**

- Multi-skill orchestration optimization — the unit of analysis is one skill at a time.
- Real-time / online optimization — batch only, against captured trace corpora.
- Skills relying on stateful long-running tools — replay assumes deterministic tool surfaces between runs.
- Paired statistical significance tests — sample sizes are too small to report p-values without theater. Deltas use replay-variance bands; read them as directional.

---


## 3. Starting hypotheses


Each waste pattern is encoded as a falsifiable detector hypothesis with its own verification protocol — a prediction of the form *"if X reproduces in traces, then Y mutation reduces some dimension without regressing equivalence."* Each detector carries a `min_occurrences` threshold so single-trace observations don't fire — a pattern earns a Finding only when it recurs across the corpus.


| ID | If trace shows... | Then... | Detect · Mutate · Ship |
|---|---|---|---|
| D001 | Same `(tool_name, hash(input))` recurs ≥`min_occurrences` times across traces | Preload that file/result in the prompt — cost drops, equivalence preserved | Tier 1 · Tier 1 · Build |
| D002 | `cache_read_input_tokens / total_input_tokens` ratio is below threshold across traces (weighted by model pricing) | Add `cache_control` annotation — cost drops on subsequent runs | Tier 1 · Tier 1 · Future |
| D003 | `tool_result.is_error: true` followed by similar `tool_use` (`difflib.SequenceMatcher` ratio on `input` dicts) | LLM rewrites tool-use guidance — reliability improves | Tier 1 · Tier 2 · Build |
| D004 | LLM judges turn's reasoning to be Haiku-sufficient given inputs/outputs observed | Swap model in frontmatter to smaller tier — cost drops, equivalence preserved | Tier 2 · Tier 1 · Build |
| D005 | Output field is byte-identical across every replay of an input across N traces | `step_determinize` embeds a deterministic helper in the rewritten SKILL.md (`partial` / `full_primary` modes); `full` mode (drop SKILL.md → plain `optimized.py`) is planned | Tier 2 · Tier 2 · Build |
| D006 | Repeated install/download patterns in Bash `tool_use` inputs across sessions | LLM rewrites cache strategy | Tier 1 · Tier 2 · Build |
| D007 | LLM judges prompt sections to be ornamental vs load-bearing-instruction | LLM rewrites prompt preserving load-bearing | Tier 2 · Tier 2 · Build |
| D008 | Sequential `tool_use` blocks have no input-output data dependency | LLM rewrites SKILL.md to emit parallel tool calls; the parallelization-oriented rewrite restructures the workflow into a tighter shape, and the measurable cost win comes from this rewrite. Actual concurrent execution stays runtime-blocked (hence *pseudo-*; see LIMITATIONS) | Tier 1 · Tier 2 · Build |
| D009 | `Read.numLines` returned ≫ lines actually referenced in subsequent thinking/text | LLM grep-narrow rewrite — cost drops | hybrid · Tier 2 · Future |
| D010 | Long `thinking` blocks (token threshold) on subsequently-trivial decisions | LLM thinking-removal rewrite — cost drops | Tier 2 · Tier 2 · Future |
| D011 | `attachment.deferred_tools_delta` registers tools never invoked in `tool_use` | Narrow `allowedTools` in SKILL.md frontmatter | Tier 1 · Tier 1 · Future |

> **Tier 1** = deterministic (rule-based, no LLM call); **Tier 2** = LLM-driven (requires an LLM call); **hybrid** = mixed within the same stage.
> **Build** = implemented in this project; **Future** = framework supports it but not built in this scope.

Each row is a starting hypothesis, not a guarantee. Some are expected not to fire on the demo traces, some to produce noisy findings that need threshold tuning, and some mutations to fail verification more often than expected. FINDINGS.md tracks which hold up.

Detector-by-detector findings — which hypotheses held against real traces, which were refined, which were rejected — accumulate in [FINDINGS.md](./FINDINGS.md) as runs complete.

---

## 4. What gets measured

Three families of measurements, gathered through the build:

**Per detector** (against labeled fixtures + real traces):

- Precision — of N findings emitted, how many describe genuine waste vs. false positive
- Recall — of M known-waste fixtures, how many were caught
- Inter-trace pattern strength — recurred across `min_occurrences` or single-trace noise

**Per mutation** (against held-out replay):

- Cost delta — token-cost difference with replay-variance bands, weighted by model pricing
- Latency delta — wall-clock and per-turn (p50, p95)
- Quality delta — structural-equivalence rate for typed fields; LLM-judge similarity score for free-text
- Reliability delta — tool-error rate, retry rate

**Per decision** (across the run):

- AUTO_APPLY / FLAG / REJECT distribution
- Calibration threshold occupancy — how many decisions sit near a boundary
- Pipeline-stage timing (detect / propose / verify / decide)

**Explicitly not measured.** Statistical significance via paired-bootstrap or McNemar. Sample sizes are too small (tens of unique inputs per skill, N=3 replays per verification on the held-out split). Observed deltas are reported with replay-variance bands — read them as directional.

---

## 5. Verification methodology

The verifier is the safety gate. Detection finds candidates and mutations write them, but nothing reaches AUTO_APPLY without the verifier signing off — so a wrong call here invalidates everything upstream.

**Held-out replay split.** Each skill's trace corpus is split 70/30 (deterministic, seeded). Detection runs on the 70%; verification replays both baseline and patched skills against the 30%. The split is recorded in every OptimizationReport so anyone can audit reproducibility.

**Replay-variance-aware equivalence.** LLM outputs are non-deterministic. Each held-out input is replayed N=3 times for both baseline and patched; the verifier compares mean-of-means with a signed-band gate over the per-replay deltas.

**Equivalence today.** Strict-eq on declared `primary_fields` ([DESIGN-DECISIONS.md ADR-003](./DESIGN-DECISIONS.md)). The `primary_fields` set is resolved in three tiers — frontmatter declaration → auto-derive from baseline replay stability → all top-level keys (see ADR-002). Non-equivalence on any declared field → REJECT.

**Equivalence extensions (future work).** LLM-judge similarity as a fallback gate for free-text fields (would recover skills like `report_drafter` whose prose outputs aren't byte-stable); numeric tolerance bands for floating-point fields. Infrastructure for the LLM-judge path is in place (`LLMClient` port); wiring is future work — see [LIMITATIONS.md](./LIMITATIONS.md) Section "Equivalence is baseline-equivalence."

**No exploratory loop.** LLM-driven rewriters get one attempt; if the verifier rejects, the proposal is REJECTed. A bounded refinement retry — one re-attempt with the failure reason — is on the future-work list, not in scope for this submission.

---

## 6. Calibration approach

Decision verdicts (AUTO_APPLY / FLAG / REJECT) are gated by 16 numeric knobs in [`config/calibration.yaml`](../config/calibration.yaml). The loader (`load_calibration()`) overlays YAML on top of frozen defaults in `src/skill_optimizer/config/calibration.py`; missing keys fall back to defaults, unknown keys are ignored.

### The 16 knobs

| Knob | Default | What it gates |
|---|---:|---|
| `min_cost_win_pct` | 10.0 | Cumulative cost must drop ≥ this % to AUTO_APPLY (below → FLAG; equivalence regression → REJECT). |
| `d001_min_occurrences` | 3 | RedundantLookup — pattern must recur across this many traces. |
| `d001_intra_trace_min` | 2 | RedundantLookup — within one trace, a (tool, input) must repeat ≥ this many times. |
| `d003_min_occurrences` | 2 | ToolReliability — failure patterns are rarer; lower floor. |
| `d003_similarity_threshold` | 0.5 | ToolReliability — difflib ratio above which failure-retry pairs count as "same call retried." |
| `d004_min_occurrences` | 3 | ModelTier — every trace must use a large-tier model this many times. |
| `d004_tier_latency_pct` | -40.0 | ModelTier — assumed latency drop for one-tier downgrade (Anthropic public throughput ratio). |
| `d005_min_inputs` | 2 | Determinism — need this many distinct inputs with stable replays. |
| `d005_min_replays` | 2 | Determinism — each input needs ≥ this many captures. |
| `d006_min_occurrences` | 2 | EnvSetupRepeat — recurring install/download patterns. |
| `d007_min_chars` | 1800 | PromptTightening — SKILL.md below this size isn't worth an LLM tightening pass. |
| `d007_trim_fraction` | 0.25 | PromptTightening — assumed removable share (cost-estimation only). |
| `d008_min_occurrences` | 3 | Pseudoparallelization — sequential `tool_use` patterns. |
| `d012_min_occurrences` | 3 | ScriptReDerivation — same function re-authored across this many runs. |
| `verifier_n_replays` | 3 | Replays per holdout input (N=3 → signed variance band). |
| `verifier_replay_timeout_s` | 240 | Per-replay SDK timeout; raise for long-context skills. |

### Tunability

The loader (`load_calibration()`) overlays YAML values on the frozen defaults, so every knob is tunable per deployment without code changes. Defaults today are pragmatic — chosen from observed demo-skill trace characteristics — and the infrastructure supports a sweep against an outcome metric on a held-out corpus as the next calibration step.

---

## 7. Demo skill set as evidence

Eight demo skills ship under `demo/skills/`, exercising different waste profiles and output shapes across the target platform's named workflow areas and adjacent structured-output cases. Three representative examples:

| Skill | Workflow | Output shape |
|---|---|---|
| `ticket_router` | Routing tickets | Categorical (target team + reason) |
| `contract_clause_flagger` | Reviewing contracts | Structured list `{clause, risk, rationale}` |
| `report_drafter` | Drafting reports | Long-form text |

 The waste in these demo skills is **seeded intentionally** — an already-optimal demo gives the optimizer nothing to find. Read the deltas as evidence of the optimizer's detect / mutate / verify loop working, not as a claim about how wasteful production skills tend to be.

Per-skill input coverage and diversity dimensions are tracked in the trace manifests under `traces/<skill>/<mode>/manifest.json` and rolled up in [EVALUATION.md](./EVALUATION.md) as the corpus grows.

---

## 8. Success criteria

What "this worked" looks like at submission:

**MVP-floor:**
- Full pipeline runs end-to-end on `ticket_router`: traces → detect → propose → verify → decide → audit → report
- At least four detectors fire (drawn from D001, D003, D004, D005, D006, D007, D008, D012) with verifiable findings
- Four-quadrant report (cost / latency / quality / reliability) emitted as `optimization_report.json` (canonical machine-readable artifact)
- Audit trail JSONL is queryable and human-readable

**Submission-floor:**
- All three demo skills optimized with documented per-skill OptimizationReports
- Calibration thresholds tuned empirically (results in [EVALUATION.md](./EVALUATION.md))
- Held-out evaluation discipline: every finding's verification ran on traces the detector never saw
- Documentation reflects what was built — [FINDINGS.md](./FINDINGS.md) captures shifts; [LIMITATIONS.md](./LIMITATIONS.md) documents gaps

**What would invalidate the approach:**
- Detectors fire on noise — false-positive AUTO_APPLY decisions when audited manually
- Verifier passes a regressing mutation — false-pass on intentionally broken mutation fixtures
- Calibration thresholds are unprincipled — most decisions sit near a boundary; threshold isn't discriminating


Per-tier acceptance evidence accumulates in [FINDINGS.md](./FINDINGS.md) and [EVALUATION.md](./EVALUATION.md) as the build progresses.
