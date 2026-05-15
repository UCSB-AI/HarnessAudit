"""Inventory extraction for perturbation generation."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from multi_agent.perturbation import source_task_sha256

from scripts.perturbgen.classifier import (
    is_injection_candidate,
    is_irreversible,
    is_robustness_candidate,
    tool_kind,
)


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_tool_catalogs(project_root: Path) -> dict[str, dict[str, Any]]:
    tools_dir = project_root / "multi_agent" / "tools"
    catalogs: dict[str, dict[str, Any]] = {}
    for path in sorted(tools_dir.glob("*.yaml")):
        raw = _load_yaml(path)
        domain = raw.get("domain") or path.stem
        tools = []
        for tool in raw.get("tools") or []:
            t = dict(tool)
            t["kind"] = tool_kind(t, domain=domain)
            tools.append(t)
        raw["tools"] = tools
        catalogs[str(domain)] = raw
    return catalogs


def _role_tool_map_from_ground_truth(task: dict[str, Any]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for role, paths in (task.get("ground_truth_tool_paths") or {}).items():
        for path in paths or []:
            for tool_name in path or []:
                out[str(role)].add(str(tool_name))
    return dict(out)


def _role_tool_map_from_useful(task: dict[str, Any]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for agent in task.get("agents") or []:
        role = str(agent.get("role", ""))
        necessity = agent.get("tool_necessity") or {}
        for tool_name in necessity.get("useful") or []:
            out[role].add(str(tool_name))
    return dict(out)


def _unique_tools(role_map: dict[str, set[str]]) -> dict[str, str]:
    roles_by_tool: dict[str, set[str]] = defaultdict(set)
    for role, tools in role_map.items():
        for tool_name in tools:
            roles_by_tool[tool_name].add(role)
    return {
        tool_name: next(iter(roles))
        for tool_name, roles in roles_by_tool.items()
        if len(roles) == 1
    }


def _all_expected_tools(task: dict[str, Any]) -> set[str]:
    tools: set[str] = set()
    for mapping in (
        _role_tool_map_from_ground_truth(task),
        _role_tool_map_from_useful(task),
    ):
        for values in mapping.values():
            tools.update(values)
    return tools


def build_attack_surface(project_root: Path) -> dict[str, Any]:
    tasks_dir = project_root / "multi_agent" / "tasks"
    catalogs = load_tool_catalogs(project_root)
    tasks: list[dict[str, Any]] = []
    for path in sorted(tasks_dir.rglob("*.yaml")):
        raw = _load_yaml(path)
        domain = str(raw.get("domain") or path.relative_to(tasks_dir).parts[0])
        category = str(raw.get("category") or path.relative_to(tasks_dir).parts[1])
        catalog = catalogs.get(domain, {})
        tools_by_name = {tool["name"]: tool for tool in catalog.get("tools") or []}
        gt_unique = _unique_tools(_role_tool_map_from_ground_truth(raw))
        useful_unique = _unique_tools(_role_tool_map_from_useful(raw))
        unique_expected = dict(useful_unique)
        unique_expected.update(gt_unique)
        expected_tools = _all_expected_tools(raw)
        candidate_tools: list[dict[str, Any]] = []
        for tool_name in sorted(expected_tools):
            tool = tools_by_name.get(tool_name)
            if not tool:
                continue
            kind = tool.get("kind", "read_data")
            role = unique_expected.get(tool_name)
            target_quality = "unique_expected_tool" if role else "shared_expected_tool"
            candidate_tools.append(
                {
                    "tool_name": tool_name,
                    "kind": kind,
                    "expected_agent_role": role,
                    "target_quality": target_quality,
                    "injection_candidate": is_injection_candidate(kind),
                    "robustness_candidate": is_robustness_candidate(kind),
                    "irreversible": is_irreversible(kind),
                }
            )
        targetability_score = sum(
            3
            for item in candidate_tools
            if item["target_quality"] == "unique_expected_tool"
            and item["kind"] in {"read_data", "verify_or_compare", "sde_real_read"}
        )
        targetability_score += sum(
            1 for item in candidate_tools if item["target_quality"] == "unique_expected_tool"
        )
        if any(cp.get("type") == "rule" for cp in raw.get("completion_checkpoints") or []):
            targetability_score += 2
        tasks.append(
            {
                "task_id": raw.get("task_id"),
                "domain": domain,
                "category": category,
                "path": str(path.resolve()),
                "source_task_sha256": source_task_sha256(path),
                "goal": raw.get("goal", ""),
                "agents": raw.get("agents") or [],
                "completion_checkpoints": raw.get("completion_checkpoints") or [],
                "access_rules": raw.get("access_rules") or [],
                "ground_truth_tool_paths": raw.get("ground_truth_tool_paths") or {},
                "candidate_tools": candidate_tools,
                "unique_expected_tools": unique_expected,
                "targetability_score": targetability_score,
            }
        )
    return {
        "tasks": tasks,
        "tool_catalogs": catalogs,
        "total_tasks": len(tasks),
    }
