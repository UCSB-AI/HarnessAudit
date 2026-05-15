"""V2: Verify all tool references exist in the domain catalog."""

from __future__ import annotations

from typing import Any

from scripts.taskgen.existing_inventory import DomainInventory
from scripts.taskgen.yaml_io import read_yaml
from pathlib import Path


def validate_tool_coverage(yaml_path: Path, inventory: DomainInventory) -> list[str]:
    """Check all tool names referenced in the task exist in the catalog."""
    errors: list[str] = []
    data = read_yaml(yaml_path)
    catalog_tools = inventory.tool_names
    task_id = data.get("task_id", yaml_path.stem)

    # Check tool_necessity
    for agent in data.get("agents", []):
        role = agent.get("role", "?")
        tn = agent.get("tool_necessity", {})
        for tier in ("useful", "forbidden", "unnecessary"):
            for tool in tn.get(tier, []):
                if tool not in catalog_tools:
                    errors.append(
                        f"{task_id} agent={role} {tier} tool '{tool}' not in catalog"
                    )

    # Check ground_truth_tool_paths
    for role, paths in (data.get("ground_truth_tool_paths") or {}).items():
        for path in paths:
            for tool in path:
                if tool not in catalog_tools:
                    errors.append(
                        f"{task_id} ground_truth_tool_paths[{role}] tool '{tool}' not in catalog"
                    )

    # Check completion_checkpoints tool_called
    for cp in data.get("completion_checkpoints", []):
        if cp.get("rule_type") == "tool_called":
            tool = cp.get("tool_name", "")
            if tool and tool not in catalog_tools:
                errors.append(
                    f"{task_id} checkpoint '{cp.get('name')}' references tool '{tool}' not in catalog"
                )

    return errors
