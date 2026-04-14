#!/usr/bin/env python3
"""汇总 5 个 domain 的 privacy / access / operation / information 分数。

支持的 domain:
  - fin
  - ec
  - healthcare
  - off
  - sde

文件名示例: sde2-t5_oai-gpt-5.4-37.json
  task_id    = sde2-t5
  model_slug = gpt-5.4   (oai- 与末尾 -<run> 之间)
  run        = 37
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = ROOT_DIR / "results"
TASKS_DIR = ROOT_DIR / "tasks"
if len(sys.argv) > 1:
    RESULTS_DIR = Path(sys.argv[1])

KEYS = ["privacy_score", "access_score", "operation_score", "information_score"]
DEFAULT_MODEL_SLUGS = ["gpt-5.4", "gpt-5.1"]
DOMAIN_ORDER = ["fin", "ec", "healthcare", "off", "sde"]
TASK_DIRS_BY_DOMAIN = {
    "fin": ["finance"],
    "ec": ["ecommerce"],
    "healthcare": ["healthcare"],
    "off": ["office", "office_enterprise_management"],
    "sde": ["sde"],
}


def parse_result_name(path: Path) -> tuple[str, str, int] | None:
    stem = path.stem
    if "_oai-" not in stem:
        return None
    task, rest = stem.split("_oai-", 1)
    run_str = rest.rsplit("-", 1)[-1]
    if not run_str.isdigit():
        return None
    model_slug = rest[: -(len(run_str) + 1)]
    if not model_slug:
        return None
    return task, model_slug, int(run_str)


def infer_domain(task_id: str) -> str | None:
    if task_id.startswith("fin-t"):
        return "fin"
    if task_id.startswith("ec-t"):
        return "ec"
    if (
        task_id.startswith("healthcare-t")
        or task_id.startswith("health-t")
        or task_id.startswith("hc-t")
    ):
        return "healthcare"
    if task_id.startswith("off-t"):
        return "off"
    if task_id.startswith("sde"):
        return "sde"
    return None


def task_sort_key(task_id: str) -> tuple[str, int | str]:
    prefix, sep, suffix = task_id.rpartition("-t")
    if sep and suffix.isdigit():
        return prefix, int(suffix)
    return task_id, task_id


def load_expected_tasks(tasks_dir: Path) -> dict[str, list[str]]:
    tasks_by_domain: dict[str, list[str]] = {domain: [] for domain in DOMAIN_ORDER}
    for domain, subdirs in TASK_DIRS_BY_DOMAIN.items():
        task_ids: set[str] = set()
        for subdir in subdirs:
            domain_dir = tasks_dir / subdir
            if not domain_dir.is_dir():
                continue
            for path in domain_dir.glob("*.yaml"):
                task_ids.add(path.stem)
        tasks_by_domain[domain] = sorted(task_ids, key=task_sort_key)
    return tasks_by_domain


def collect_latest_results(
    results_dir: Path,
) -> tuple[
    dict[tuple[str, str], tuple[int, Path]],
    dict[str, set[str]],
    list[str],
]:
    latest_by_task_model: dict[tuple[str, str], tuple[int, Path]] = {}
    discovered_tasks: dict[str, set[str]] = {domain: set() for domain in DOMAIN_ORDER}
    seen_models: set[str] = set()

    for path in sorted(results_dir.glob("*_oai-gpt-*.json")):
        parsed = parse_result_name(path)
        if not parsed:
            continue
        task_id, model_slug, run_id = parsed
        domain = infer_domain(task_id)
        if domain is None:
            continue
        seen_models.add(model_slug)
        discovered_tasks[domain].add(task_id)
        key = (task_id, model_slug)
        if key not in latest_by_task_model or run_id > latest_by_task_model[key][0]:
            latest_by_task_model[key] = (run_id, path)

    extra_models = sorted(seen_models.difference(DEFAULT_MODEL_SLUGS))
    model_slugs = DEFAULT_MODEL_SLUGS + extra_models
    return latest_by_task_model, discovered_tasks, model_slugs


def load_result(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def score_as_float(value: object, default: float = 0.0) -> float:
    """将 JSON 中的分数字段转为 float；无法转换时用 default。"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def main() -> None:
    if not RESULTS_DIR.is_dir():
        print(f"错误: results 目录不存在: {RESULTS_DIR}", file=sys.stderr)
        sys.exit(1)

    expected_tasks = load_expected_tasks(TASKS_DIR)
    latest_by_task_model, discovered_tasks, model_slugs = collect_latest_results(RESULTS_DIR)

    if not latest_by_task_model:
        print(f"警告: 在 {RESULTS_DIR} 下未匹配到支持的结果文件", file=sys.stderr)

    for domain in DOMAIN_ORDER:
        task_ids = sorted(
            set(expected_tasks[domain]).union(discovered_tasks[domain]),
            key=task_sort_key,
        )

        print(f"## {domain}")
        print("task\tmodel_slug\tllm\t" + "\t".join(KEYS))
        if not task_ids:
            print("(暂无 task / result)")
        else:
            for task_id in task_ids:
                for model_slug in model_slugs:
                    key = (task_id, model_slug)
                    if key not in latest_by_task_model:
                        print(
                            f"{task_id}\t{model_slug}\tMISSING\t"
                            + "\t".join(["-"] * len(KEYS))
                        )
                        continue
                    _, path = latest_by_task_model[key]
                    data = load_result(path)
                    llm = data.get("llm", "")
                    vals = [f"{score_as_float(data.get(name, 0)):.4f}" for name in KEYS]
                    print(f"{task_id}\t{model_slug}\t{llm}\t" + "\t".join(vals))

        print("\n# 按 model_slug 平均（有缺失则只平均存在的 task）")
        for model_slug in model_slugs:
            acc = {name: [] for name in KEYS}
            for task_id in task_ids:
                key = (task_id, model_slug)
                if key not in latest_by_task_model:
                    continue
                data = load_result(latest_by_task_model[key][1])
                for name in KEYS:
                    acc[name].append(score_as_float(data.get(name, 0)))
            if not acc["privacy_score"]:
                print(f"{model_slug}: (无数据)")
                continue
            n = len(acc["privacy_score"])
            parts = [f"{name}={sum(acc[name]) / n:.4f}" for name in KEYS]
            print(f"{model_slug}  n={n}: " + "  ".join(parts))
        print()


if __name__ == "__main__":
    main()