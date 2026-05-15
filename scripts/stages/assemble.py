"""Assemble stage outputs into final task YAML files."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from scripts.taskgen.existing_inventory import DomainInventory
from scripts.taskgen.yaml_io import write_yaml

log = logging.getLogger(__name__)


def assemble_task_yaml(
    scenario: dict,
    fixtures: dict,
    rules: dict,
    checkpoints: list,
    assets: list | None,
    *,
    all_tool_names: set[str] | None = None,
) -> dict[str, Any]:
    """Combine all stage outputs into a single task YAML dict."""
    task_id = scenario["task_id"]
    domain = scenario.get("domain", "")
    category = scenario.get("category", "")

    gt_paths = rules.get("ground_truth_tool_paths", {})

    # Build agents with tool_necessity and communication_policy from Stage 3
    agents = []
    tool_necessity_map = rules.get("tool_necessity", {})
    comm_policy_map = rules.get("communication_policy", {})
    for agent in scenario.get("agents", []):
        role = agent["role"]
        agent_entry: dict[str, Any] = {
            "role": role,
            "description": agent.get("description", ""),
            "system_prompt": agent.get("system_prompt", ""),
        }
        # Merge tool_necessity: prefer Stage 3 output, fall back to Stage 1
        tn = tool_necessity_map.get(role, agent.get("tool_necessity", {}))
        if tn:
            agent_entry["tool_necessity"] = dict(tn)
        # Merge communication_policy: prefer Stage 3 output, fall back to Stage 1
        cp = comm_policy_map.get(role, agent.get("communication_policy"))
        if cp and isinstance(cp, dict):
            # Only include if at least one of allowed/forbidden is non-empty
            if cp.get("allowed") or cp.get("forbidden"):
                agent_entry["communication_policy"] = cp
        agents.append(agent_entry)

    task: dict[str, Any] = {
        "task_id": task_id,
        "domain": domain,
        "category": category,
        "modality": scenario.get("modality", "text"),
        "goal": scenario.get("goal", ""),
        "agents": agents,
        "access_rules": rules.get("access_rules", []),
        "completion_checkpoints": checkpoints,
        "ground_truth_tool_paths": gt_paths,
        "metadata": scenario.get("metadata", {}),
    }

    # Add input_assets for multimodal tasks
    if assets:
        task["input_assets"] = [
            {
                "asset_type": a.get("asset_type", "image"),
                "path": f"assets/{task_id}/{a.get('filename', 'asset.png')}",
                "description": a.get("description", ""),
            }
            for a in assets
        ]

    return _sanitize_task(task, all_tool_names=all_tool_names)


# ---------------------------------------------------------------------------
# Post-LLM sanitization — deterministic fixes for known LLM output issues
# ---------------------------------------------------------------------------

_VALID_SEVERITIES = {"low", "high"}
_VALID_ACCESS_TYPES = {
    "operation.tool_call",
    "operation.resource",
    "information.communication",
    "information.data_leak",
}
_ACCESS_TYPE_FIXES = {
    "operation.tool": "operation.tool_call",
}

# Placeholder values that should never appear in metadata or goal text
_PLACEHOLDER_RE = re.compile(
    r"\bNONE\b|<placeholder>|\bTBD\b|\bN/A\b",
    re.IGNORECASE,
)


def _sanitize_task(
    task: dict[str, Any],
    *,
    all_tool_names: set[str] | None = None,
) -> dict[str, Any]:
    """Fix known LLM output issues before writing YAML.

    Deterministic repairs for fields with a finite set of valid values.
    Anything the LLM gets wrong that we can fix mechanically goes here.
    """
    task_id = task.get("task_id", "?")

    # ── modality: "text" → "text_only" ──
    if task.get("modality") == "text":
        task["modality"] = "text_only"

    # ── access_rules ──
    # Strip LLM-generated information.communication rules (V-IC rules are
    # auto-synthesized from communication_policy by the loader)
    task["access_rules"] = [
        r for r in task.get("access_rules", [])
        if r.get("access_type") != "information.communication"
    ]
    for rule in task["access_rules"]:
        rule.pop("effect", None)  # field removed from schema
        if rule.get("severity") not in _VALID_SEVERITIES:
            rule["severity"] = "low"
        at = rule.get("access_type", "")
        if at in _ACCESS_TYPE_FIXES:
            rule["access_type"] = _ACCESS_TYPE_FIXES[at]

    # ── Recompute useful = union(ground_truth_tool_paths[role]) ──
    gt_paths = task.get("ground_truth_tool_paths", {})
    for agent in task.get("agents", []):
        role = agent.get("role", "")
        tn = agent.get("tool_necessity")
        if not tn:
            continue
        if role in gt_paths:
            union_tools = set()
            for path in gt_paths[role]:
                union_tools.update(path)
            old_useful = set(tn.get("useful", []))
            if old_useful != union_tools:
                log.debug(
                    "  %s agent=%s: useful corrected %s → %s",
                    task_id, role, sorted(old_useful), sorted(union_tools),
                )
                tn["useful"] = sorted(union_tools)
        elif tn.get("useful"):
            # Hub or agent with no ground_truth — useful should be empty
            log.debug(
                "  %s agent=%s: no gt_paths, clearing useful %s",
                task_id, role, tn.get("useful"),
            )
            tn["useful"] = []

    # ── Recompute unnecessary = all_tools - useful - forbidden ──
    if all_tool_names:
        for agent in task.get("agents", []):
            tn = agent.get("tool_necessity")
            if not tn:
                continue
            useful = set(tn.get("useful", []))
            forbidden = set(tn.get("forbidden", []))
            computed_unnecessary = sorted(all_tool_names - useful - forbidden)
            tn.pop("unnecessary", None)  # remove explicit unnecessary
            if computed_unnecessary:
                tn["unnecessary"] = computed_unnecessary

    # ── completion_checkpoints ──
    cps = task.get("completion_checkpoints", [])
    for cp in cps:
        # Fix type: "sql_assert" → "rule" + rule_type: "sql_assert"
        if cp.get("type") == "sql_assert":
            cp["type"] = "rule"
            cp.setdefault("rule_type", "sql_assert")

        # Convert deprecated expected_min → expect
        if "expected_min" in cp and "expect" not in cp:
            emin = cp.pop("expected_min")
            emax = cp.pop("expected_max", None)
            if emax is not None and emin == 0:
                cp["expect"] = f"<= {emax}"
            elif emin == 0:
                cp["expect"] = "0"
            else:
                cp["expect"] = f">= {emin}"
        elif "expected_min" in cp:
            cp.pop("expected_min")  # expect already present

    # Normalize checkpoint weights to sum to 1.0
    weights = [cp.get("weight", 0) for cp in cps]
    total = sum(weights)
    if cps and abs(total - 1.0) > 0.01:
        factor = 1.0 / total if total > 0 else 1.0
        for cp in cps:
            cp["weight"] = round(cp.get("weight", 0) * factor, 4)
        remainder = 1.0 - sum(cp["weight"] for cp in cps)
        cps[-1]["weight"] = round(cps[-1]["weight"] + remainder, 4)

    # ── NONE placeholder detection in metadata and goal ──
    metadata = task.get("metadata", {})
    for key, val in list(metadata.items()):
        if isinstance(val, str) and _PLACEHOLDER_RE.fullmatch(val.strip()):
            log.warning(
                "  %s: metadata.%s has placeholder value '%s'", task_id, key, val,
            )

    goal = task.get("goal", "")
    if _PLACEHOLDER_RE.search(goal):
        log.warning("  %s: goal text contains placeholder value", task_id)

    # Remove violation_design_notes (internal, not part of final YAML)
    task.pop("violation_design_notes", None)

    return task


def assemble_all(
    inventory: DomainInventory,
    scenarios: list[dict],
    fixtures: dict[str, dict],
    rules: dict[str, dict],
    checkpoints: dict[str, list],
    assets: dict[str, list],
    staging_dir: Path,
) -> list[Path]:
    """Assemble all tasks and write YAML files. Returns list of written paths."""
    log.info("Assembling %d task YAMLs...", len(scenarios))
    written: list[Path] = []

    for scenario in scenarios:
        task_id = scenario["task_id"]
        category = scenario.get("category", "general")

        task_yaml = assemble_task_yaml(
            scenario=scenario,
            fixtures=fixtures.get(task_id, {}),
            rules=rules.get(task_id, {}),
            checkpoints=checkpoints.get(task_id, []),
            assets=assets.get(task_id),
            all_tool_names=inventory.tool_names,
        )

        # Early schema pre-check (warn, don't block)
        try:
            from multi_agent.schemas.task import TaskConfig
            TaskConfig(**task_yaml)
        except Exception as exc:
            log.warning("  %s: schema pre-check failed: %s", task_id, exc)

        # Write to canonical location
        out_dir = inventory.config.tasks_dir / category
        out_path = out_dir / f"{task_id}.yaml"
        write_yaml(out_path, task_yaml)
        written.append(out_path)
        log.info("  Wrote %s", out_path)

    log.info("Assembly complete: %d YAML files written", len(written))
    return written
