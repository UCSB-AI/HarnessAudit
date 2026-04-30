"""V4: Verify required ∪ forbidden ∪ unnecessary == all_domain_tools, mutually exclusive."""

from __future__ import annotations

from pathlib import Path

from scripts.taskgen.existing_inventory import DomainInventory
from scripts.taskgen.yaml_io import read_yaml


def validate_tool_partition(yaml_path: Path, inventory: DomainInventory) -> list[str]:
    """Check that tool_necessity is a complete, disjoint partition of the catalog."""
    errors: list[str] = []
    data = read_yaml(yaml_path)
    task_id = data.get("task_id", yaml_path.stem)
    all_tools = inventory.tool_names

    for agent in data.get("agents", []):
        role = agent.get("role", "?")
        tn = agent.get("tool_necessity", {})
        required = set(tn.get("required", []))
        forbidden = set(tn.get("forbidden", []))
        unnecessary = set(tn.get("unnecessary", []))

        # If unnecessary is not explicitly listed, derive it
        if not unnecessary and (required or forbidden):
            unnecessary = all_tools - required - forbidden

        union = required | forbidden | unnecessary

        # Check completeness
        missing = all_tools - union
        if missing:
            errors.append(
                f"{task_id} agent={role} tools not in any tier: {missing}"
            )

        # Check no extras (tools not in catalog)
        extra = union - all_tools
        if extra:
            errors.append(
                f"{task_id} agent={role} tools not in catalog: {extra}"
            )

        # Check mutual exclusivity
        rf = required & forbidden
        if rf:
            errors.append(f"{task_id} agent={role} in both required & forbidden: {rf}")
        ru = required & unnecessary
        if ru:
            errors.append(f"{task_id} agent={role} in both required & unnecessary: {ru}")
        fu = forbidden & unnecessary
        if fu:
            errors.append(f"{task_id} agent={role} in both forbidden & unnecessary: {fu}")

    return errors
