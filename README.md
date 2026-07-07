# F-01 — Execution-Log-Driven Skill Optimization

A system that reads Claude Code execution traces, identifies where a skill spends more than it needs to (cost, latency, reliability), and rewrites the skill to close those gaps without regressing output quality.

**Input file:** your original `SKILL.md` (+ captured traces).
**Result file:** the rewritten `SKILL.md` is stored at `runs/<timestamp>/optimized/<skill>/SKILL.md` — diff it against the original to see exactly what the optimizer changed (previous vs new).

Most files here are test data, not code:

- `demo/skills/` — 8 demo skills, with the optimizer run end-to-end on each one.
- `traces/` — recorded runs for 4 of those skills. Traces are what the optimizer reads: it spots where a skill wastes tokens or turns, and rewrites the skill to fix it.

To try this without recording new traces, run `skopt optimize` on one of the 4 demos that ship with captured traces — see *"Quick — on a shipped demo"* below.

## Pipeline

`detect → propose → verify → decide → audit → report`

| Stage | What it produces |
|---|---|
| **detect** | findings against `SKILL.md` + traces (D001–D012 detector hypotheses, deterministic + LLM-judged) |
| **propose** | mutations — Tier-1 deterministic templates, Tier-2 LLM-driven rewrites |
| **verify** | held-out replay evidence — N=3 runs of baseline and patched on a 70/30 seeded split |
| **decide** | AUTO_APPLY / FLAG / REJECT verdict per mutation, gated by calibration thresholds |
| **audit** | JSONL trail of every decision and the evidence behind it |
| **report** | four-quadrant summary: cost, latency, quality, reliability |


## Layout

```
.
├── pyproject.toml
├── .env.example                   # CLAUDE_CONFIG_DIR + optional dev SDK key
├── config/
│   ├── pricing.yaml               # Anthropic public rates per model × bucket
│   └── calibration.yaml           # detector thresholds + gate cutoffs
├── src/skill_optimizer/
│   ├── domain/                    # pure logic; no I/O
│   ├── ports/                     # abstract interfaces
│   ├── adapters/                  # concrete impls (Claude Agent SDK, filesystem)
│   └── cli/                       # `skopt` entrypoint
├── docs/                          # APPROACH, ARCHITECTURE, DESIGN-DECISIONS, LIMITATIONS, FINDINGS
├── demo/skills/                   # 8 shipped demo skills (invoice_validator, loan_calculator, ...)
├── traces/                        # captured runs for 4 of the 8 demos — input to `skopt optimize`
├── tests/{unit,integration}/
├── scripts/
│   ├── check.py                   # six-step runtime + auth diagnostic
│   ├── capture_traces.py          # capture session JSONLs into the corpus
│   └── inspect_trace_sdk.py       # SDK runtime smoke test
└── runs/                          # per-invocation output (auto-generated)
```

## Runtime

LLM calls run via the [Claude Agent SDK](https://docs.anthropic.com/en/api/agent-sdk) (`claude_agent_sdk`) — in-process. Authentication uses Claude Team login; no Anthropic API key required (and none accepted) for the evaluated path.

## First-time setup

The optimizer authenticates via a Claude Team seat using OAuth. To keep that seat cleanly isolated from any other Claude account already logged into `~/.claude`, the project uses a dedicated config dir at `~/.aklaude`. Both seats coexist on disk; the active one is whichever `CLAUDE_CONFIG_DIR` is set in the current shell.

**One-time login.** In a fresh terminal (not inside another Claude Code session):

```bash
CLAUDE_CONFIG_DIR=~/.aklaude claude
```

Inside the REPL: `/login`. Complete the OAuth flow with the Team seat, then `/exit`.

**Verify.**

```bash
python scripts/check.py
```

Six-step diagnostic: Python version → config dir → `claude` binary → SDK install → auth ping → tool-use ping. A green `VERDICT: GREEN` confirms the setup. Re-run any time the runtime stops working — the script branches to actionable advice on each failure mode.

**Re-login when sessions expire.** OAuth sessions periodically expire. When `check.py` reports `FAIL` on the auth ping, repeat the one-time login above; the rest of the setup persists.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python scripts/check.py            # confirm auth + tool use; see First-time setup if it fails
```

A green `check.py` confirms the SDK runtime path can talk to the Team seat.

## Running the optimizer

### Quick — on a shipped demo

**Traces are shipped for four demo skills.** Ready to run `skopt optimize` against out-of-box, no capture step needed:

| Skill | Workflow area | What it demonstrates |
|---|---|---|
| `demo/skills/invoice_validator` | Invoices | D012 helper extraction + ground-truth gate (12/12 PASS); −69.9% cumulative cost |
| `demo/skills/loan_calculator` | Financial calculation | Generalization headline (−85.8% cumulative cost); 12/12 ground-truth PASS on 6 clean + 6 fault-injected inputs |
| `demo/skills/release_notes_compiler` | Reports | D003 + D006 + D008 triple AUTO_APPLY |
| `demo/skills/contract_redline_reviewer` | Contracts | D001 multi-pass re-read first-fire |

```bash
skopt optimize \
  --skill demo/skills/invoice_validator \
  --traces-root traces \
  --holdout 1
```

Swap `--skill` for any of the four above. Output lands in `runs/<timestamp>/`.

For any other demo skill (`ticket_router`, `contract_clause_flagger`, `report_drafter`, `policy_compliance_auditor`), capture traces first with `python scripts/capture_traces.py --skill demo/skills/<name>`.

### Bring your own skill

**1. Place the skill at any path:**

```
/any/path/your_skill/
├── SKILL.md            # `name:` required; `primary_fields:` strongly recommended (see below)
├── sample_inputs/      # files or directory bundles the skill operates on
│   └── input_001.json
└── expected_outputs/   # OPTIONAL — for ground-truth verification (see LIMITATIONS)
    └── input_001.json
```

> **`primary_fields:` — declare it in your SKILL.md frontmatter.** It's the list of output JSON keys the verifier compares to decide whether a patched skill agrees with the baseline (e.g. `primary_fields: ["valid", "computed"]`). Without it, the optimizer falls back to auto-deriving from baseline replay stability — works, but explicit author intent is more reliable. Full explanation in *["What the verifier needs to know about the skill — `primary_fields`"](#what-the-verifier-needs-to-know-about-the-skill--primary_fields)* below.

> **Note — `expected_outputs/` is optional, not required.** Two of the shipped demos (`invoice_validator`, `loan_calculator`) include hand-authored `expected_outputs/<input>.json` alongside their `sample_inputs/`. These are the reference oracle for `scripts/d012_helper_groundtruth.py`, which runs the optimizer's extracted `helper.py` against every input and compares the full output object — an independent signal alongside the verifier's primary-field check. **BYO skills don't need this.** It's only feasible when expected outputs are mechanically derivable from inputs (arithmetic, labeled routing, etc.); skills with free-text or non-deterministic outputs simply don't ship one. See [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) Section "Equivalence is baseline-equivalence" for the upgrade-path framing.

If the inputs live elsewhere (not under the skill dir), pass `--inputs-dir /path/to/inputs`. If the skill writes its answer to a non-default filename (e.g. `result.json`), declare it in frontmatter:

```yaml
---
name: your-skill
output_path: result.json    # optional; defaults to "output.json"
---
```

**2. Place the traces in any folder.** Two layouts both work:

```
# Easiest — flat: just JSONLs in one folder
/any/path/my_traces/
├── session_001.jsonl       # raw Claude Code SDK JSONL — that's all
├── session_002.jsonl
└── ...

# Or structured — multi-skill, what `capture_traces.py` writes
/any/path/traces/
└── your_skill/baseline/
    ├── session_001.jsonl
    └── ...
```

Pass that folder to `--traces-root`. The optimizer auto-detects flat vs. structured.

**Your traces must follow these two rules — otherwise the optimizer can't run on them:**

1. **Each JSONL contains a `Write` to `output.json`** (or to whatever filename is declared in the `output_path:` frontmatter above). That file is the skill's output for that input. Without it, there's no baseline for the optimizer to compare its rewrites against.

2. **Each JSONL's first message contains the text `sample_inputs/<filename>`.** That's how the optimizer figures out which input file each trace ran against. The path around it can be anything — `sample_inputs/case_001.json`, `/var/tmp/.../sample_inputs/case_001.json`, `../sample_inputs/case_001.json` all work; only the `sample_inputs/<filename>` substring matters.

If production traces don't include this substring (e.g. prompts referenced inputs as `inputs/case_001.json`, or pasted the input content directly into the prompt), the optimizer can't map traces to inputs. The pipeline will appear to run, but the verifier will produce meaningless results.

`manifest.json` and parallel `output.json` files are accepted if present but never required.

If no traces have been captured yet, capture them:

```bash
python scripts/capture_traces.py --skill /any/path/your_skill
# Writes traces/<your_skill_name>/baseline/ — 3 replays per input by default.
```

**3. Run the optimizer:**

```bash
skopt optimize \
  --skill /any/path/your_skill \
  --traces-root /any/path/traces \
  --holdout 1
```

**4. Inspect the result** in `runs/<timestamp>/`:
- `decisions.jsonl` — one record per mutation (AUTO_APPLY / FLAG / REJECT + gate math)
- `optimization_report.json` — full report
- `optimized/<your_skill_name>/` — the composed optimized skill

### What the verifier needs to know about the skill — `primary_fields`

The equivalence check is *"do the patched and baseline outputs agree on the fields that matter?"* Declare which fields matter in the SKILL.md's YAML frontmatter:

```yaml
---
name: your-skill
model: claude-haiku-4-5
primary_fields: ["valid", "computed"]      # ← the keys the verifier compares
---
```

If `primary_fields` isn't declared, the optimizer resolves it in three tiers (in order):

1. **SKILL.md frontmatter declaration** (above) — author intent wins. Deterministic and auditable.
2. **Auto-derive from baseline replays** — if the captured baseline corpus has ≥2 replays per input, the optimizer infers the stable fields by intersecting "keys whose values are identical across replays of the same input" across all inputs. Deterministic given a fixed corpus; matches what a skill author would have chosen for most JSON-output skills.
3. **All top-level keys of the first baseline output** — the most-conservative fallback. Strict-eq on every field. Patches that touch any output field fail equivalence — which is the safe direction.

## Calibration

Sixteen numeric knobs in [`config/calibration.yaml`](config/calibration.yaml) — cost gates, per-detector occurrence floors, verifier replay parameters. Override any knob by editing the YAML; the loader overlays YAML values on top of frozen defaults in `src/skill_optimizer/config/calibration.py`. Common knobs:

- `min_cost_win_pct: 10.0` — AUTO_APPLY requires cumulative cost to drop by at least this %; below this → FLAG or REJECT.
- `d00X_min_occurrences` — per-detector cross-trace recurrence floor (defaults 2 or 3; a pattern must recur this many times before the detector fires).
- `verifier_n_replays: 3` — replays per holdout input on both baseline and patched; N=3 gives the signed variance band.
- `verifier_replay_timeout_s: 240` — per-replay SDK timeout (raise for long-context skills).

Defaults are pragmatic — chosen from demo-skill trace characteristics; the loader overlays YAML on frozen defaults, so every knob is tunable per deployment. See [`docs/APPROACH.md`](docs/APPROACH.md) Section 6 *Calibration* for the rationale.

## Documentation

- [`docs/APPROACH.md`](docs/APPROACH.md) — scope, detector hypotheses, verification methodology, calibration approach, success criteria.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — domain model, ports/adapters, evaluation harness, cost accounting, audit trail.

## F-01 specification coverage

Direct mapping from the F-01 catalog's seven optimization categories to the architecture's detector roster (per [`docs/APPROACH.md`](docs/APPROACH.md)).

| # | Spec category | Spec wording (FEATURE-CATALOG.md F-01) | Detector | Mutation | Tier (Detect · Mutate) |
|---|---|---|---|---|---|
| 1 | Extract reusable scripts | *"stop re-deriving the same code inside the prompt every run; persist it as a callable artifact"* | **D012** ScriptReDerivation | `helper_extract` | Tier 2 · Tier 2 |
| 2 | Persist environment setup | *"detect tools, dependencies, or resources being installed or fetched on every run and cache them between executions"* | **D006** EnvSetupRepeat | `cache_strategy_rewrite` | Tier 1 · Tier 2 |
| 3 | Catch tool execution misses | *"identify tool calls that fail and get retried ... and add guidance that prevents the failure mode next time"* | **D003** ToolReliability | `tool_guidance_rewrite` | Tier 1 · Tier 2 |
| 4 | Eliminate redundant lookups | *"when the same file, query, or piece of context is fetched multiple times in a single run, pre-load it or deduplicate"* | **D001** RedundantLookup | `preload_file` | Tier 1 · Tier 1 |
| 5a | Tighten instructions *(prompts and guidance)* | *"shorter prompts ... sharper guidance"* | **D007** PromptTightening | `prompt_rewrite` | Tier 1 · Tier 2 |
| 5b | Tighten instructions *(round-trips)* | *"fewer round-trips"* | **D008** Pseudoparallelization | `pseudoparallelize_tools` | Tier 1 · Tier 2 |
| 6 | Downgrade the model | *"route steps to a cheaper tier when quality doesn't suffer"* | **D004** ModelTier | `model_swap` | Tier 2 · Tier 1 |
| 7 | Replace LLM steps with deterministic logic | *"where outputs are stable across runs, swap reasoning for code"* | **D005** Determinism | `step_determinize` | Tier 2 · Tier 2 |

**Tier legend.** 1 = deterministic template, no LLM. 2 = LLM-driven via the `LLMClient` port. *hybrid* = Tier 1 short-circuit + Tier 2 confirmation.

**Cat 1 vs Cat 7.** D012 targets *code the model re-derives every run* (persists as `helper.py`); D005 targets *reasoning whose output is stable across runs*.

**D012 ground-truth check.** Skills with derivable expected outputs ship an `expected_outputs/` dir; `scripts/d012_helper_groundtruth.py` runs the extracted `helper.py` against every input and compares the full output.
