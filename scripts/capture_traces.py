"""Capture traces (baseline or diverse) for any skill at a path.

Run from repo root:
    # baseline mode — N replays per input, supports cross-trace + variance signals
    python scripts/capture_traces.py --skill demo/skills/ticket_router

    # diverse mode — 1 run per input, supports cross-input pattern signals
    python scripts/capture_traces.py --skill demo/skills/ticket_router --mode diverse

    # single-input verify mode — output to _verify/, no clobber of baseline
    python scripts/capture_traces.py --skill demo/skills/ticket_router --input ticket_001.txt

The skill directory is identified entirely by its filesystem path. Per-skill
metadata (input glob, summary field) is read from SKILL.md frontmatter; both
default to sensible values so a brand-new skill works without edits.

Output paths (under traces/, keyed by ``skill_dir.name``):
    baseline   → traces/<skill>/baseline/
    diverse    → traces/<skill>/diverse/   (forces n_runs=1)
    --input    → traces/<skill>/_verify/   (single trace, regardless of --mode)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

from skill_optimizer.domain.trace import extract_output_from_trace, parse_trace_file
from skill_optimizer.preflight import preflight_or_exit
from skill_optimizer.skill_md import (
    build_replay_prompt,
    read_input_glob,
    read_output_path,
    read_summary_field,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_SECONDS = 240
INTER_RUN_SLEEP = 1.0


def session_dir_for_cwd(cwd: Path, config_dir: Path) -> Path:
    sanitized = str(cwd).replace("/", "-").replace("_", "-")
    return config_dir / "projects" / sanitized


def skill_cli_model(skill_dir: Path) -> str | None:
    """The `claude --model` alias for the SKILL.md's declared model (haiku/sonnet/opus),
    or None. Without it the CLI's Sonnet default would mismatch the verifier's replay."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None
    in_frontmatter = False
    for line in skill_md.read_text().splitlines():
        if line.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            break
        if in_frontmatter and line.lstrip().startswith("model:"):
            declared = line.split(":", 1)[1].strip()
            if declared.startswith("claude-haiku"):
                return "haiku"
            if declared.startswith("claude-sonnet"):
                return "sonnet"
            if declared.startswith("claude-opus"):
                return "opus"
            return declared or None
    return None


def newest_jsonl_in(dir_path: Path, since_epoch: float) -> Path | None:
    if not dir_path.exists():
        return None
    candidates = [p for p in dir_path.rglob("*.jsonl") if p.stat().st_mtime > since_epoch]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def run_one(
    claude_path: str,
    env: dict[str, str],
    input_path: Path,
    skill_dir: Path,
    summary_field: str | None,
    output_path: str,
    run_idx: int,
    config_dir: Path,
    out_dir: Path,
    cli_model: str | None = None,
    filename_base: str | None = None,
) -> dict:
    """Invoke claude CLI on one sample input, return summary dict for the run."""
    expected_session_dir = session_dir_for_cwd(skill_dir, config_dir)
    started_at = time.time()
    prompt = build_replay_prompt(skill_dir, input_path.name, output_path=output_path)
    base = filename_base if filename_base is not None else f"run_{run_idx:03d}"

    print(f"\n[{run_idx:03d}] {input_path.name}")
    print(f"      prompt: {prompt}")

    cmd = [claude_path, "--allowedTools", "Read Edit Write"]
    if cli_model:
        cmd += ["--model", cli_model]
    cmd += ["-p", prompt]
    try:
        result = subprocess.run(
            cmd,
            cwd=skill_dir,
            capture_output=True,
            text=True,
            env=env,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"      TIMEOUT after {TIMEOUT_SECONDS}s")
        return {"run": run_idx, "input": input_path.name, "status": "timeout"}

    elapsed = time.time() - started_at

    if result.returncode != 0:
        print(f"      FAILED rc={result.returncode}")
        print(f"      stderr: {result.stderr.strip()[:500]}")
        print(f"      stdout: {result.stdout.strip()[:1000]}")
        return {
            "run": run_idx,
            "input": input_path.name,
            "status": "failed",
            "returncode": result.returncode,
            "elapsed_s": round(elapsed, 2),
        }

    jsonl = newest_jsonl_in(expected_session_dir, started_at)
    if jsonl is None:
        jsonl = newest_jsonl_in(config_dir / "projects", started_at)
    if jsonl is None:
        print("      no jsonl found")
        return {
            "run": run_idx,
            "input": input_path.name,
            "status": "no_trace",
            "elapsed_s": round(elapsed, 2),
        }

    out_jsonl = out_dir / f"{base}.jsonl"
    shutil.copyfile(jsonl, out_jsonl)

    output_json_src = skill_dir / output_path
    out_output = out_dir / f"{base}.output.json"
    summary_value: object = "<missing>"
    output_source: str | None = None  # "file" | "jsonl" | None
    output_filename = Path(output_path).name

    # Prefer the file the model wrote; fall back to extracting from the JSONL's
    # Write tool call. The model sometimes finishes without persisting the output
    # file (esp. on classification skills where it answers in text), but the trace
    # captures the intended Write — we recover it the same way the trace store does.
    if output_json_src.exists():
        shutil.copyfile(output_json_src, out_output)
        output_source = "file"
        try:
            parsed = json.loads(output_json_src.read_text())
        except json.JSONDecodeError:
            parsed = None
            summary_value = "<unparseable>"
    else:
        try:
            extracted = extract_output_from_trace(
                parse_trace_file(out_jsonl), output_filename=output_filename,
            )
        except (ValueError, OSError):
            extracted = None
        if extracted is not None:
            out_output.write_text(json.dumps(extracted, indent=2))
            output_source = "jsonl"
            parsed = extracted
        else:
            parsed = None

    if parsed is not None and summary_field:
        summary_value = parsed.get(summary_field, "<missing>")

    if output_source and parsed is not None:
        if summary_field:
            status_str = f"{summary_field}={summary_value} [{output_source}]"
        else:
            preview = ", ".join(f"{k}={v!r}" for k, v in list(parsed.items())[:2])
            more = "..." if len(parsed) > 2 else ""
            status_str = f"output ok ({preview}{more}) [{output_source}]"
    elif output_source:
        status_str = f"output <unparseable> [{output_source}]"
    else:
        status_str = "output <missing> — model never produced a Write to output.json"
    print(f"      ok in {elapsed:.1f}s  → {out_jsonl.name}  {status_str}")
    summary = {
        "run": run_idx,
        "input": input_path.name,
        "status": "ok",
        "elapsed_s": round(elapsed, 2),
        "trace": out_jsonl.name,
        "output": out_output.name if output_source else None,
        "output_source": output_source,
    }
    if summary_field:
        summary[summary_field] = summary_value
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture baseline traces for a skill at a path.")
    parser.add_argument(
        "--skill",
        required=True,
        type=Path,
        help="Path to the skill directory (containing SKILL.md and sample_inputs/).",
    )
    parser.add_argument(
        "--mode",
        choices=["baseline", "diverse"],
        default="baseline",
        help=(
            "baseline (default): N replays per input → traces/<skill>/baseline/. "
            "diverse: forces 1 run per input → traces/<skill>/diverse/. "
            "Ignored when --input is set."
        ),
    )
    parser.add_argument(
        "--n-runs",
        type=int,
        default=3,
        help=(
            "Number of replays per input. Default 3 — gives the verifier's auto-derive "
            "tier enough data to identify byte-stable fields. "
            "Forced to 1 in diverse mode and --input mode."
        ),
    )
    parser.add_argument(
        "--input",
        help=(
            "Capture only this single sample input (filename or directory name). "
            "Output is written to traces/<skill>/_verify/ so the baseline/diverse "
            "corpora are not modified. --n-runs and --mode are ignored in this mode."
        ),
    )
    parser.add_argument(
        "--traces-root",
        type=Path,
        default=REPO_ROOT / "traces",
        help=(
            "Root directory for trace output. Defaults to traces/. "
            "Symmetric with optimize's --traces-root."
        ),
    )
    args = parser.parse_args()

    load_dotenv()

    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.aklaude")).expanduser()
    if not config_dir.exists():
        print(f"ERROR: CLAUDE_CONFIG_DIR ({config_dir}) does not exist.", file=sys.stderr)
        return 1

    claude_path = shutil.which("claude")
    if not claude_path:
        print("ERROR: `claude` CLI not found on PATH.", file=sys.stderr)
        return 1

    skill_dir = args.skill.resolve()
    if not skill_dir.exists():
        print(f"ERROR: skill dir not found: {skill_dir}", file=sys.stderr)
        return 1
    if not (skill_dir / "SKILL.md").exists():
        print(f"ERROR: SKILL.md not found in {skill_dir}", file=sys.stderr)
        return 1

    skill_name = skill_dir.name
    cli_model = skill_cli_model(skill_dir)
    sample_inputs_dir = skill_dir / "sample_inputs"
    input_glob = read_input_glob(skill_dir)
    summary_field = read_summary_field(skill_dir)
    output_path = read_output_path(skill_dir)
    traces_root = args.traces_root.resolve()
    baseline_out_dir = traces_root / skill_name / "baseline"
    diverse_out_dir = traces_root / skill_name / "diverse"
    verify_out_dir = traces_root / skill_name / "_verify"

    if not sample_inputs_dir.exists():
        print(f"ERROR: sample_inputs/ not found in {skill_dir}", file=sys.stderr)
        return 1

    inputs = sorted(sample_inputs_dir.glob(input_glob))
    if args.input:
        inputs = [p for p in inputs if p.name == args.input]
        if not inputs:
            print(
                f"ERROR: --input {args.input!r} not found in {sample_inputs_dir}",
                file=sys.stderr,
            )
            return 1

    if not inputs:
        print(
            f"ERROR: no sample inputs matching {input_glob!r} in {sample_inputs_dir}",
            file=sys.stderr,
        )
        return 1

    if args.input:
        out_dir = verify_out_dir
        n_runs = 1
        mode_label = "verify (single-input)"
    elif args.mode == "diverse":
        out_dir = diverse_out_dir
        n_runs = 1
        mode_label = "diverse"
        if args.n_runs != 3:
            print(f"  (--mode diverse forces n_runs=1; --n-runs={args.n_runs} ignored)")
    else:
        out_dir = baseline_out_dir
        n_runs = args.n_runs
        mode_label = "baseline"

    out_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)

    # Mirror the env in this process too, so the SDK ping in preflight uses
    # the same auth path that the spawned `claude` subprocesses below will use.
    os.environ["CLAUDE_CONFIG_DIR"] = str(config_dir)
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

    # Fail fast on auth expiry — one trivial SDK ping vs. N silent subprocess
    # failures during the capture loop.
    preflight_or_exit()

    total = len(inputs) * n_runs
    print(
        f"capturing {total} trace(s) from {skill_name} "
        f"[{mode_label}] ({len(inputs)} inputs × {n_runs} replays)"
    )
    print(f"skill dir : {skill_dir}")
    print(f"out dir   : {out_dir}")
    print(f"glob      : {input_glob}")
    summary_label = summary_field or "(none — no summary_field / primary_fields)"
    print(f"summary   : {summary_label}")
    model_label = cli_model or "(CLI default)"
    model_source = "from SKILL.md" if cli_model else "no model: declared"
    print(f"model     : {model_label} ({model_source})")

    # Per-run tempdir copy so model-authored files don't pollute the source skill_dir.
    summaries: list[dict] = []
    run_idx = 1
    for input_path in inputs:
        for _ in range(n_runs):
            filename_base = input_path.stem if args.input else None
            with tempfile.TemporaryDirectory(prefix=f"capture-{skill_name}-") as tmp_root:
                run_skill_dir = Path(tmp_root) / skill_name
                shutil.copytree(skill_dir, run_skill_dir)
                summaries.append(run_one(
                    claude_path=claude_path,
                    env=env,
                    input_path=input_path,
                    skill_dir=run_skill_dir,
                    summary_field=summary_field,
                    output_path=output_path,
                    run_idx=run_idx,
                    config_dir=config_dir,
                    out_dir=out_dir,
                    cli_model=cli_model,
                    filename_base=filename_base,
                ))
            time.sleep(INTER_RUN_SLEEP)
            run_idx += 1

    manifest = out_dir / "manifest.json"
    manifest.write_text(json.dumps({"runs": summaries}, indent=2))
    print(f"\nwrote {manifest}")

    ok = sum(1 for s in summaries if s.get("status") == "ok")
    print(f"summary: {ok}/{len(summaries)} runs successful")
    return 0 if ok == len(summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
