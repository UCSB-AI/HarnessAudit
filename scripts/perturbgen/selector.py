"""Stratified task sampling and target selection."""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any

DEFAULT_DOMAIN_TARGETS = {
    "daily_life": 11,
    "ecommerce": 19,
    "finance": 20,
    "healthcare": 15,
    "legal_compliance": 10,
    "office": 13,
    "software_engineering": 5,
    "social_interaction": 12,
}

DEFAULT_SEED = "masp-l3-perturb-v1"

AUTH_TOOL_RE = re.compile(r"(auth|authenticate|login|identity|verify_customer)", re.I)

INJECTION_TOOL_PRIORITY = {
    "run_security_scan": 0,
    "check_budget_coverage": 1,
    "calculate_order_adjustment": 1,
    "compare_service_plans": 1,
    "run_tests": 2,
    "read_source_code": 2,
    "search_codebase": 2,
    "get_order_details": 2,
    "get_customer_context": 3,
    "get_customer_profile": 3,
    "review_service_option": 3,
    "get_product_details": 4,
    "read_error_logs": 9,
}

ROBUSTNESS_TOOL_PRIORITY = {
    "run_security_scan": 0,
    "run_tests": 1,
    "check_budget_coverage": 1,
    "calculate_order_adjustment": 1,
    "get_order_details": 1,
    "compare_service_plans": 2,
    "read_source_code": 2,
    "search_codebase": 2,
    "get_product_details": 3,
    "review_service_option": 3,
    "get_customer_context": 4,
    "get_customer_profile": 4,
    "read_error_logs": 9,
}


def stable_hash(value: str, *, seed: str = DEFAULT_SEED) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode("utf-8")).hexdigest()


def _allocate_by_category(tasks: list[dict[str, Any]], target: int) -> dict[str, int]:
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        by_category[str(task["category"])].append(task)
    total = len(tasks)
    raw: list[tuple[str, int, float]] = []
    assigned = 0
    for category, items in by_category.items():
        exact = target * len(items) / total
        floor = int(exact)
        raw.append((category, floor, exact - floor))
        assigned += floor
    remaining = target - assigned
    raw.sort(key=lambda x: (-x[2], x[0]))
    alloc = {category: floor for category, floor, _rem in raw}
    for category, _floor, _rem in raw[:remaining]:
        alloc[category] += 1
    return alloc


def sample_tasks(
    attack_surface: dict[str, Any],
    *,
    domain_targets: dict[str, int] | None = None,
    seed: str = DEFAULT_SEED,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    domain_targets = domain_targets or DEFAULT_DOMAIN_TARGETS
    tasks = attack_surface["tasks"]
    selected: list[dict[str, Any]] = []
    report: dict[str, Any] = {"domains": {}, "selected_total": 0}
    for domain, target in domain_targets.items():
        domain_tasks = [task for task in tasks if task["domain"] == domain]
        alloc = _allocate_by_category(domain_tasks, min(target, len(domain_tasks)))
        selected_ids: list[str] = []
        for category, count in alloc.items():
            category_tasks = [task for task in domain_tasks if task["category"] == category]
            category_tasks.sort(
                key=lambda item: (
                    -int(item.get("targetability_score", 0)),
                    stable_hash(str(item["task_id"]), seed=seed),
                )
            )
            picked = category_tasks[:count]
            selected.extend(picked)
            selected_ids.extend(str(task["task_id"]) for task in picked)
        report["domains"][domain] = {
            "target": target,
            "selected": len(selected_ids),
            "category_allocation": alloc,
            "task_ids": sorted(selected_ids),
        }
    report["selected_total"] = len(selected)
    selected.sort(key=lambda item: (item["domain"], item["category"], item["task_id"]))
    return selected, report


def select_targets(task: dict[str, Any]) -> dict[str, Any]:
    candidates = task.get("candidate_tools") or []
    domain = str(task.get("domain", ""))

    def base_key(item: dict[str, Any], priority: dict[str, int]) -> tuple[int, int, int, str]:
        name = str(item.get("tool_name", ""))
        unique = 0 if item.get("target_quality") == "unique_expected_tool" else 1
        readish = 0 if item.get("kind") in {"read_data", "verify_or_compare", "sde_real_read"} else 1
        return (priority.get(name, 5), unique, readish, name)

    def injection_sort_key(item: dict[str, Any]) -> tuple[int, int, int, int, str]:
        name = str(item.get("tool_name", ""))
        sde_low_delivery_risk = 1 if domain == "software_engineering" and name == "read_error_logs" else 0
        priority, unique, readish, tool_name = base_key(item, INJECTION_TOOL_PRIORITY)
        return (sde_low_delivery_risk, priority, unique, readish, tool_name)

    def robustness_sort_key(item: dict[str, Any]) -> tuple[int, int, int, int, str]:
        name = str(item.get("tool_name", ""))
        # Authentication tools often need user-provided identity fields.  They
        # are valid injection surfaces but poor required robustness targets.
        auth_low_delivery_risk = 1 if AUTH_TOOL_RE.search(name) else 0
        sde_low_delivery_risk = 1 if domain == "software_engineering" and name == "read_error_logs" else 0
        priority, unique, readish, tool_name = base_key(item, ROBUSTNESS_TOOL_PRIORITY)
        return (auth_low_delivery_risk, sde_low_delivery_risk, priority, unique, readish, tool_name)

    injection = [
        item for item in candidates
        if item.get("injection_candidate") and item.get("target_quality") == "unique_expected_tool"
    ]
    if len(injection) < 2:
        injection.extend(
            item for item in candidates
            if item.get("injection_candidate") and item not in injection
        )
    injection = sorted(injection, key=injection_sort_key)[:3]

    robustness = [
        item for item in candidates
        if item.get("robustness_candidate")
        and not item.get("irreversible")
        and item.get("target_quality") == "unique_expected_tool"
    ]
    if not robustness:
        robustness = [
            item for item in candidates
            if item.get("robustness_candidate") and not item.get("irreversible")
        ]
    robustness = sorted(robustness, key=robustness_sort_key)[:1]

    agents = task.get("agents") or []
    low_agent = next((a for a in agents[1:] if a.get("system_prompt")), agents[1] if len(agents) > 1 else agents[0])
    return {
        "task_id": task["task_id"],
        "domain": task["domain"],
        "injection_targets": injection,
        "robustness_targets": robustness,
        "ambiguity_high": {"level": "high", "target_agent_role": None},
        "ambiguity_low": {
            "level": "low",
            "target_agent_role": low_agent.get("role") if low_agent else None,
        },
    }


def target_selection_report(selected_tasks: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [select_targets(task) for task in selected_tasks]
    by_domain = Counter(task["domain"] for task in selected_tasks)
    return {
        "selected_total": len(selected_tasks),
        "by_domain": dict(by_domain),
        "tasks": rows,
    }
