from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from masp.completion_judge import (
    COMPLETION_SCORE_MODE,
    evaluate_completion_checkpoints,
)
from masp.loader import load_task
from masp.schemas.actions import ObservableAction


DEFAULT_TASK_IDS: tuple[str, ...] = ()
DEFAULT_MODELS = (
    "gpt-5.4",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "gemini-3.1-pro-preview",
    "glm-5v-turbo",
    "kimi-k2.6",
    "qwen3.5-plus",
)


class SQLiteBank:
    """Minimal bank adapter for evaluating sql_assert checkpoints offline."""

    def __init__(self, path: Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row

    def query_sql(self, query: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(query)
        return [dict(row) for row in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()


@dataclass(slots=True)
class OfflineWorkspace:
    path: Path


@dataclass(slots=True)
class RejudgeJob:
    model: str
    result_path: Path
    task_path: Path
    task_id: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _task_index(task_root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for path in task_root.rglob("*.yaml"):
        if path.name == "role_merge_map_ecommerce_social_v1.yaml":
            continue
        try:
            task = load_task(path)
        except Exception:
            continue
        out[task.task_id] = path
    return out


def _result_paths_for_task(result_dir: Path, task_id: str) -> list[Path]:
    matches: list[Path] = []
    for path in result_dir.glob(f"{task_id}_*.json"):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if data.get("task_id") == task_id:
            matches.append(path)
    return sorted(matches)


def _result_paths_for_model(result_dir: Path) -> list[Path]:
    matches: list[Path] = []
    for path in result_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if data.get("task_id"):
            matches.append(path)
    return sorted(matches)


def _has_existing_tcr(data: dict[str, Any]) -> bool:
    required_keys = (
        "task_completion_score",
        "deterministic_completion_score",
        "deterministic_completion_weight",
        "deterministic_completion_contribution",
        "llm_completion_score",
        "llm_completion_weight",
        "llm_completion_contribution",
        "trajectory_tcr",
        "completion_score_mode",
    )
    if data.get("completion_score_mode") != COMPLETION_SCORE_MODE:
        return False
    for key in required_keys:
        if key not in data:
            return False
    return data.get("trajectory_tcr") is not None


def _load_actions_and_final_output(trace_path: Path) -> tuple[list[ObservableAction], str]:
    actions: list[ObservableAction] = []
    final_output = ""
    for line in trace_path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") not in {"tool_call", "communication"}:
            continue
        action = ObservableAction.model_validate(row)
        actions.append(action)
        target = action.target_role or action.target_agent
        if action.surface == "communication" and target == "user":
            final_output = action.content or ""
    return actions, final_output


def _resolve_trace_path(repo_root: Path, result_path: Path, trace_file: str) -> Path:
    trace_path = repo_root / trace_file
    if trace_path.is_file():
        return trace_path

    results_root = repo_root / "masp" / "results"
    traces_root = repo_root / "masp" / "traces"
    filename = Path(trace_file).name

    try:
        rel = result_path.relative_to(results_root)
    except ValueError:
        rel = None

    if rel is not None and len(rel.parts) >= 3:
        harness, model = rel.parts[0], rel.parts[1]
        candidate = traces_root / harness / model / filename
        if candidate.is_file():
            return candidate

    matches = sorted(traces_root.rglob(filename))
    if matches:
        return matches[0]

    raise FileNotFoundError(trace_path)


def _checkpoint_needs_workspace(task: Any) -> bool:
    workspace_rule_types = {"file_contains", "file_not_contains", "pytest_passes"}
    for checkpoint in getattr(task, "completion_checkpoints", []) or []:
        if (
            checkpoint.get("type") == "rule"
            and checkpoint.get("rule_type") in workspace_rule_types
        ):
            return True
    return False


def _resolve_workspace(repo_root: Path, data: dict[str, Any], task: Any) -> OfflineWorkspace | None:
    workspace_path = data.get("workspace_path")
    if workspace_path:
        path = Path(str(workspace_path)).expanduser()
        if path.is_dir():
            return OfflineWorkspace(path=path)

    run_id = data.get("run_id")
    fixture = getattr(task, "fixture", None)
    if not run_id or not fixture:
        return None

    workspaces_root = Path(
        os.environ.get(
            "MASP_WORKSPACES_ROOT",
            str(repo_root / "masp" / "workspaces"),
        )
    )
    candidate = workspaces_root / str(run_id) / str(fixture)
    if candidate.is_dir():
        return OfflineWorkspace(path=candidate)
    return None


async def _rejudge_one(
    *,
    result_path: Path,
    task_path: Path,
    repo_root: Path,
    judge_model: str,
    judge_workers: int,
) -> dict[str, Any]:
    data = json.loads(result_path.read_text())
    trace_file = data.get("trace_file")
    if not trace_file:
        raise ValueError(f"{result_path} has no trace_file")
    trace_path = _resolve_trace_path(repo_root, result_path, trace_file)

    task = load_task(task_path)
    actions, final_output = _load_actions_and_final_output(trace_path)
    workspace = _resolve_workspace(repo_root, data, task)
    if _checkpoint_needs_workspace(task) and workspace is None:
        return {
            "task_id": task.task_id,
            "model": result_path.parent.name,
            "tcr": None,
            "completion_score_mode": COMPLETION_SCORE_MODE,
            "skipped": True,
            "skip_reason": "missing_workspace",
            "output": str(result_path.relative_to(repo_root)),
        }

    sqlite_path = result_path.with_suffix(".sqlite")
    bank = SQLiteBank(sqlite_path) if sqlite_path.exists() else None
    try:
        completion = await evaluate_completion_checkpoints(
            task,
            actions,
            final_output,
            bank=bank,
            workspace=workspace,
            judge_model=judge_model,
            skip_judge=False,
            judge_workers=judge_workers,
        )
    finally:
        if bank is not None:
            bank.close()

    new_data = dict(data)
    new_data["task_completion_score"] = completion.task_completion_score
    new_data["deterministic_completion_score"] = (
        completion.deterministic_completion_score
    )
    new_data["deterministic_completion_weight"] = (
        completion.deterministic_completion_weight
    )
    new_data["deterministic_completion_contribution"] = (
        completion.deterministic_completion_contribution
    )
    new_data["llm_completion_score"] = completion.llm_completion_score
    new_data["llm_completion_weight"] = completion.llm_completion_weight
    new_data["llm_completion_contribution"] = completion.llm_completion_contribution
    new_data["trajectory_tcr"] = completion.task_completion_score
    new_data["completion_score_mode"] = COMPLETION_SCORE_MODE
    if workspace is not None:
        new_data["workspace_path"] = str(workspace.path)

    # The new TCR schema is authoritative; remove legacy/debug side fields.
    for stale_key in (
        "task_completed",
        "old_task_completion_score",
        "source_result_file",
        "final_output_only_tcr",
        "final_output_tcr",
        "final_output_chars",
        "completion_sqlite_used",
        "completion_weight_total",
    ):
        new_data.pop(stale_key, None)
    result_path.write_text(json.dumps(new_data, indent=2) + "\n")

    return {
        "task_id": task.task_id,
        "model": result_path.parent.name,
        "tcr": completion.task_completion_score,
        "completion_score_mode": COMPLETION_SCORE_MODE,
        "deterministic_completion_score": completion.deterministic_completion_score,
        "deterministic_completion_contribution": (
            completion.deterministic_completion_contribution
        ),
        "llm_completion_score": completion.llm_completion_score,
        "llm_completion_contribution": completion.llm_completion_contribution,
        "output": str(result_path.relative_to(repo_root)),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", default="openclaw")
    parser.add_argument("--models", nargs="*", default=list(DEFAULT_MODELS))
    parser.add_argument(
        "--tasks",
        nargs="*",
        default=list(DEFAULT_TASK_IDS),
        help="Task ids to rejudge. If omitted, all result JSON files are updated.",
    )
    parser.add_argument("--exclude-models", nargs="*", default=[])
    parser.add_argument("--judge-model", default="gpt-5.4-nano")
    parser.add_argument(
        "--judge-workers",
        type=int,
        default=2,
        help="Maximum number of task-level rejudge jobs to run concurrently.",
    )
    parser.add_argument(
        "--summary-path",
        default=None,
        help="Optional path for a JSON summary. Per-task result JSONs are updated in place.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip result JSONs that already contain the current trajectory TCR fields.",
    )
    args = parser.parse_args()

    repo_root = _repo_root()
    task_paths = _task_index(repo_root / "masp" / "tasks")
    result_base = repo_root / "masp" / "results" / args.harness

    jobs: list[RejudgeJob] = []
    excluded_models = set(args.exclude_models)
    models = [model for model in args.models if model not in excluded_models]

    for model in models:
        model_dir = result_base / model
        if not model_dir.is_dir():
            print(f"MISS model dir: {model_dir}")
            continue
        result_paths: list[Path] = []
        if args.tasks:
            for task_id in args.tasks:
                matches = _result_paths_for_task(model_dir, task_id)
                if not matches:
                    print(f"MISS result: model={model} task={task_id}")
                result_paths.extend(matches)
        else:
            result_paths = _result_paths_for_model(model_dir)

        for result_path in result_paths:
            data = json.loads(result_path.read_text())
            task_id = data.get("task_id")
            if args.skip_existing and _has_existing_tcr(data):
                print(f"SKIP existing: model={model} task={task_id}", flush=True)
                continue
            task_path = task_paths.get(task_id)
            if task_path is None:
                print(f"MISS task yaml: {task_id}")
                continue
            jobs.append(
                RejudgeJob(
                    model=model,
                    result_path=result_path,
                    task_path=task_path,
                    task_id=str(task_id),
                )
            )

    semaphore = asyncio.Semaphore(max(1, args.judge_workers))

    async def run_job(job: RejudgeJob) -> dict[str, Any]:
        async with semaphore:
            started = time.perf_counter()
            print(f"REJUDGE model={job.model} task={job.task_id}", flush=True)
            row = await _rejudge_one(
                result_path=job.result_path,
                task_path=job.task_path,
                repo_root=repo_root,
                judge_model=args.judge_model,
                judge_workers=args.judge_workers,
            )
            elapsed = time.perf_counter() - started
            row["duration_seconds"] = elapsed
            if row.get("skipped"):
                print(
                    f"SKIP {row.get('skip_reason')}: "
                    f"model={job.model} task={job.task_id} "
                    f"({elapsed:.1f}s)",
                    flush=True,
                )
            else:
                print(
                    f"DONE model={job.model} task={job.task_id} "
                    f"tcr={float(row.get('tcr', 0.0)):.4f} "
                    f"({elapsed:.1f}s)",
                    flush=True,
                )
            return row

    summaries = await asyncio.gather(*(run_job(job) for job in jobs))

    by_model: dict[str, list[float]] = {}
    for row in summaries:
        if row.get("tcr") is None:
            continue
        by_model.setdefault(row["model"], []).append(float(row["tcr"]))
    print("\nTrajectory-based TCR:")
    for model in models:
        vals = by_model.get(model, [])
        if vals:
            print(f"{model:28s} n={len(vals):2d} mean={sum(vals) / len(vals):.4f}")
    if args.summary_path:
        summary_path = repo_root / args.summary_path
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summaries, indent=2) + "\n")
        print(f"\nSummary saved to {summary_path.relative_to(repo_root)}")


if __name__ == "__main__":
    asyncio.run(main())
