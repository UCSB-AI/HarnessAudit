#!/usr/bin/env python3
"""Run generated perturbation variants through ``masp_bench run``."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = PROJECT_ROOT / "masp_bench" / "perturbation_specs" / "index.jsonl"

ATTACK_ALIASES = {
    "all": "all",
    "injection": "indirect_injection",
    "indirect_injection": "indirect_injection",
    "ambiguity": "ambiguous_goal",
    "ambiguous_goal": "ambiguous_goal",
    "robustness": "robustness",
}


def _read_index(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _select_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows = _read_index(Path(args.perturbation_index))
    attack = ATTACK_ALIASES[args.attack_type]
    selected: list[dict[str, Any]] = []
    for row in rows:
        if not args.include_optional and not row.get("required", False):
            continue
        if attack != "all" and row.get("attack_type") != attack:
            continue
        if args.domain and row.get("domain") != args.domain:
            continue
        if args.task_id and row.get("task_id") != args.task_id:
            continue
        if args.variant_id and row.get("variant_id") != args.variant_id:
            continue
        selected.append(row)
    return selected


def _model_dir_name(model: str) -> str:
    model_name = (model or "unknown-model").strip() or "unknown-model"
    return model_name.replace("/", "__")


def _artifact_scoped_dir(base_dir: Path, harness: str, model: str) -> Path:
    model_dir = _model_dir_name(model)
    if base_dir.name == model_dir and base_dir.parent.name == harness:
        return base_dir
    if base_dir.name == harness:
        return base_dir / model_dir
    return base_dir / harness / model_dir


def _resolve_repo_path(path: str | Path) -> Path:
    value = str(path)
    candidate = Path(value)
    if candidate.exists():
        return candidate
    if not candidate.is_absolute():
        return PROJECT_ROOT / candidate

    marker = "masp_bench/"
    if marker in value:
        remapped = PROJECT_ROOT / marker / value.split(marker, 1)[1]
        if remapped.exists():
            return remapped
    return candidate


def _completed_variant_ids(args: argparse.Namespace) -> set[str]:
    """Return perturbation variant IDs that already have result JSON reports."""
    output_dir = _artifact_scoped_dir(Path(args.output), args.harness, args.model)
    if not output_dir.exists():
        return set()
    completed: set[str] = set()
    for path in output_dir.glob("*.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        perturbation = report.get("perturbation")
        if not isinstance(perturbation, dict):
            continue
        if not _is_usable_completed_report(report):
            continue
        variant_id = perturbation.get("variant_id")
        if isinstance(variant_id, str) and variant_id:
            completed.add(variant_id)
    return completed


def _is_usable_completed_report(report: dict[str, Any]) -> bool:
    if report.get("error"):
        return False
    n_actions = int(report.get("n_actions") or 0)
    n_tool_calls = int(report.get("n_tool_calls") or 0)
    n_communications = int(report.get("n_communications") or 0)
    completion = float(report.get("task_completion_score") or 0.0)
    if n_actions <= 1 and n_tool_calls == 0 and n_communications <= 1 and completion == 0.0:
        return False
    return True


def _run_one(args: argparse.Namespace, row: dict[str, Any]) -> tuple[str, int]:
    cmd = [
        sys.executable,
        "-m",
        "masp_bench",
        "run",
        str(_resolve_repo_path(row["task_path"])),
        "--framework",
        "clawteam",
        "--harness",
        args.harness,
        "--model",
        args.model,
        "--agent-timeout",
        str(args.agent_timeout),
        "--max-turns",
        str(args.max_turns),
        "--judge-model",
        args.judge_model,
        "--judge-workers",
        str(args.judge_workers),
        "--output",
        args.output,
        "--trace-dir",
        args.trace_dir,
        "--perturbation-id",
        str(row["variant_id"]),
        "--perturbation-index",
        str(args.perturbation_index),
    ]
    if args.skip_judge:
        cmd.append("--skip-judge")
    if args.allow_stale_perturbation:
        cmd.append("--allow-stale-perturbation")
    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if args.log_dir:
        log_dir = Path(args.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"{row['variant_id']}.log").write_text(result.stdout, encoding="utf-8")
    return str(row["variant_id"]), result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MASP perturbation matrix")
    parser.add_argument("--perturbation-index", default=str(DEFAULT_INDEX))
    parser.add_argument("--attack-type", default="all", choices=sorted(ATTACK_ALIASES))
    parser.add_argument("--domain", default=None)
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--variant-id", default=None)
    parser.add_argument("--required-only", action="store_true", default=True,
                        help="Compatibility flag; required variants are the default")
    parser.add_argument("--include-optional", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--harness", default="openclaw", choices=["claude", "codex", "openclaw"])
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--agent-timeout", type=int, default=300)
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--judge-model", default="gpt-5.4")
    parser.add_argument("--judge-workers", type=int, default=4)
    parser.add_argument("--skip-judge", action="store_true")
    parser.add_argument("--allow-stale-perturbation", action="store_true")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip variants that already have result JSON reports for this harness/model",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "masp_bench" / "results" / "perturbations"),
    )
    parser.add_argument(
        "--trace-dir",
        default=str(PROJECT_ROOT / "masp_bench" / "traces" / "perturbations"),
    )
    parser.add_argument("--log-dir", default=None)
    args = parser.parse_args()

    rows = _select_rows(args)
    skipped = 0
    if args.skip_existing:
        completed = _completed_variant_ids(args)
        before = len(rows)
        rows = [row for row in rows if str(row.get("variant_id")) not in completed]
        skipped = before - len(rows)
    print(f"Selected {len(rows)} perturbation variants")
    if skipped:
        print(f"Skipped {skipped} existing perturbation variants")
    if not rows:
        return
    failures: list[str] = []
    passed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(_run_one, args, row) for row in rows]
        for future in concurrent.futures.as_completed(futures):
            variant_id, returncode = future.result()
            if returncode == 0:
                print(f"PASS {variant_id}")
                passed += 1
            else:
                print(f"FAIL {variant_id} rc={returncode}")
                failures.append(variant_id)
    print(
        "PERTURBATION_RUN_DONE "
        f"selected={len(rows)} skipped={skipped} passed={passed} failed={len(failures)}"
    )
    if failures:
        raise SystemExit(f"{len(failures)} perturbation runs failed")


if __name__ == "__main__":
    main()
