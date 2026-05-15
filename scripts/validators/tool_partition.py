"""V4: Verify useful ∪ forbidden ∪ unnecessary == all_domain_tools, mutually exclusive."""

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
        useful = set(tn.get("useful") or [])
        forbidden = set(tn.get("forbidden", []))
        unnecessary = set(tn.get("unnecessary", []))

        # If unnecessary is not explicitly listed, derive it
        if not unnecessary and (useful or forbidden):
            unnecessary = all_tools - useful - forbidden

        union = useful | forbidden | unnecessary

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
        uf = useful & forbidden
        if uf:
            errors.append(f"{task_id} agent={role} in both useful & forbidden: {uf}")
        uu = useful & unnecessary
        if uu:
            errors.append(f"{task_id} agent={role} in both useful & unnecessary: {uu}")
        fu = forbidden & unnecessary
        if fu:
            errors.append(f"{task_id} agent={role} in both forbidden & unnecessary: {fu}")

    return errors
