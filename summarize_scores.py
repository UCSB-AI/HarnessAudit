#!/usr/bin/env python3
"""汇总 10 个 domain 的评测分数（与 cli 写入 results/*.json 字段一致）。

metrics（Layer 1–3）:
  - access_score
  - op_privacy_score   (V-OT/V-OR；无 tool surface 时 JSON 可能为 null)
  - info_privacy_score (V-ID/V-IC；无 comm surface 时可能为 null)
  - tsr, pqsr

文件名示例: sde2-t5_oai-gpt-5.4-37.json
  task_id    = sde2-t5
  framework  = oai / ag
  model_slug = gpt-5.4   (_oai- 与末尾 -<run> 之间)
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

# 与 masp_bench/cli.py report 一致
KEYS = [
    "access_score",
    "op_privacy_score",
    "info_privacy_score",
    "tsr",
    "pqsr",
]
DEFAULT_MODEL_SLUGS = ["gpt-5.4", "gpt-5.1"]
DEFAULT_FRAMEWORK_TAGS = ["oai", "ag"]

# 汇总用短名 -> tasks 子目录
DOMAIN_ORDER = ["fin", "ec", "healthcare", "off", "sde", "dl", "jm", "lc", "si", "re"]
TASK_DIRS_BY_DOMAIN: dict[str, list[str]] = {
    "fin": ["finance"],
    "ec": ["ecommerce"],
    "healthcare": ["healthcare"],
    "off": ["office"],
    "sde": ["sde"],
    "dl": ["daily_life"],
    "jm": ["journalism_media"],
    "lc": ["legal_compliance"],
    "si": ["social_interaction"],
    "re": ["real_estate"],
}


def parse_result_name(path: Path) -> tuple[str, str, str, int] | None:
    stem = path.stem
    if "_" not in stem or "-" not in stem:
        return None
    prefix, run_str = stem.rsplit("-", 1)
    if not run_str.isdigit():
        return None
    if "_" not in prefix:
        return None
    task, fw_and_model = prefix.split("_", 1)
    if "-" not in fw_and_model:
        return None
    framework_tag, model_slug = fw_and_model.split("-", 1)
    if not framework_tag or not model_slug:
        return None
    if not task:
        return None
    return task, framework_tag, model_slug, int(run_str)


def infer_domain(task_id: str) -> str | None:
    if task_id.startswith("fin-t"):
        return "fin"
    if task_id.startswith("ec-t"):
        return "ec"
    if task_id.startswith("hc-t"):
        return "healthcare"
    if task_id.startswith("off-t"):
        return "off"
    if task_id.startswith("sde"):
        return "sde"
    if task_id.startswith("dl-t"):
        return "dl"
    if task_id.startswith("jm-t"):
        return "jm"
    if task_id.startswith("lc-t"):
        return "lc"
    if task_id.startswith("si-t"):
        return "si"
    if task_id.startswith("re-t"):
        return "re"
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
    dict[tuple[str, str, str], tuple[int, Path]],
    dict[str, set[str]],
    list[str],
    list[str],
]:
    latest_by_task_fw_model: dict[tuple[str, str, str], tuple[int, Path]] = {}
    discovered_tasks: dict[str, set[str]] = {domain: set() for domain in DOMAIN_ORDER}
    seen_frameworks: set[str] = set()
    seen_models: set[str] = set()

    for path in sorted(results_dir.glob("*.json")):
        parsed = parse_result_name(path)
        if not parsed:
            continue
        task_id, framework_tag, model_slug, run_id = parsed
        domain = infer_domain(task_id)
        if domain is None:
            continue
        seen_frameworks.add(framework_tag)
        seen_models.add(model_slug)
        discovered_tasks[domain].add(task_id)
        key = (task_id, framework_tag, model_slug)
        if key not in latest_by_task_fw_model or run_id > latest_by_task_fw_model[key][0]:
            latest_by_task_fw_model[key] = (run_id, path)

    extra_frameworks = sorted(seen_frameworks.difference(DEFAULT_FRAMEWORK_TAGS))
    framework_tags = DEFAULT_FRAMEWORK_TAGS + extra_frameworks
    extra_models = sorted(seen_models.difference(DEFAULT_MODEL_SLUGS))
    model_slugs = DEFAULT_MODEL_SLUGS + extra_models
    return latest_by_task_fw_model, discovered_tasks, framework_tags, model_slugs


def load_result(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def format_metric(value: object) -> str:
    """access/tsr/pqsr 为 float；branch 分可能为 null。"""
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)):
        return f"{float(value):.4f}"
    if isinstance(value, str):
        try:
            return f"{float(value):.4f}"
        except ValueError:
            return "n/a"
    return "n/a"


def mean_non_na(values: list[float | None]) -> str:
    xs = [v for v in values if v is not None]
    if not xs:
        return "n/a"
    return f"{sum(xs) / len(xs):.4f}"


def get_numeric(data: dict[str, object], name: str) -> float | None:
    v = data.get(name)
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def main() -> None:
    if not RESULTS_DIR.is_dir():
        print(f"错误: results 目录不存在: {RESULTS_DIR}", file=sys.stderr)
        sys.exit(1)

    matched = list(RESULTS_DIR.glob("*.json"))
    if not matched:
        print(f"警告: 在 {RESULTS_DIR} 下未匹配到 *.json", file=sys.stderr)

    expected_tasks = load_expected_tasks(TASKS_DIR)
    latest_by_task_fw_model, discovered_tasks, framework_tags, model_slugs = collect_latest_results(
        RESULTS_DIR
    )

    if not latest_by_task_fw_model:
        print(f"警告: 无支持的 domain 结果（检查 task_id 前缀）", file=sys.stderr)

    for domain in DOMAIN_ORDER:
        task_ids = sorted(
            set(expected_tasks[domain]).union(discovered_tasks[domain]),
            key=task_sort_key,
        )

        print(f"## {domain}")
        print("task\tframework\tmodel_slug\tllm\t" + "\t".join(KEYS))
        if not task_ids:
            print("(暂无 task / result)")
        else:
            for task_id in task_ids:
                for framework_tag in framework_tags:
                    for model_slug in model_slugs:
                        key = (task_id, framework_tag, model_slug)
                        if key not in latest_by_task_fw_model:
                            print(
                                f"{task_id}\t{framework_tag}\t{model_slug}\tMISSING\t"
                                + "\t".join(["-"] * len(KEYS))
                            )
                            continue
                        _, path = latest_by_task_fw_model[key]
                        data = load_result(path)
                        llm = str(data.get("llm", ""))
                        vals = [format_metric(data.get(name)) for name in KEYS]
                        print(
                            f"{task_id}\t{framework_tag}\t{model_slug}\t{llm}\t"
                            + "\t".join(vals)
                        )

        print(
            "\n# 按 framework + model 平均"
            "（branch 分含 null 时仅对非 null 求平均；缺失 task 跳过）"
        )
        for framework_tag in framework_tags:
            for model_slug in model_slugs:
                acc: dict[str, list[float | None]] = {name: [] for name in KEYS}
                for task_id in task_ids:
                    key = (task_id, framework_tag, model_slug)
                    if key not in latest_by_task_fw_model:
                        continue
                    data = load_result(latest_by_task_fw_model[key][1])
                    for name in KEYS:
                        acc[name].append(get_numeric(data, name))
                if not acc["access_score"] or all(
                    v is None for v in acc["access_score"]
                ):
                    print(f"{framework_tag}/{model_slug}: (无数据)")
                    continue
                n_tasks = len([v for v in acc["access_score"] if v is not None])
                parts = [f"{name}={mean_non_na(acc[name])}" for name in KEYS]
                print(f"{framework_tag}/{model_slug}  n={n_tasks}: " + "  ".join(parts))
        print()


if __name__ == "__main__":
    main()
