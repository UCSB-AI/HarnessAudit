"""V3: Verify useful == union(ground_truth_tool_paths[role]) for every agent."""

from __future__ import annotations

from pathlib import Path

from scripts.taskgen.yaml_io import read_yaml


def _useful_tools(tn: dict) -> set[str]:
    return set(tn.get("useful") or [])


def validate_useful_invariant(yaml_path: Path) -> list[str]:
    """Check that each role's useful set equals the union of its ground truth paths."""
    errors: list[str] = []
    data = read_yaml(yaml_path)
    task_id = data.get("task_id", yaml_path.stem)
    gt_paths = data.get("ground_truth_tool_paths") or {}

    for agent in data.get("agents", []):
        role = agent.get("role", "?")
        tn = agent.get("tool_necessity", {})
        useful = _useful_tools(tn)

        if role not in gt_paths:
            # Hub agent has no ground truth paths — useful should be empty
            if useful:
                errors.append(
                    f"{task_id} agent={role} has useful tools {useful} but no ground_truth_tool_paths entry"
                )
            continue

        paths = gt_paths[role]
        union_of_paths = set()
        for path in paths:
            union_of_paths.update(path)

        if useful != union_of_paths:
            missing = union_of_paths - useful
            extra = useful - union_of_paths
            parts = []
            if missing:
                parts.append(f"missing from useful: {missing}")
            if extra:
                parts.append(f"extra in useful: {extra}")
            errors.append(
                f"{task_id} agent={role} useful != union(ground_truth_paths): {'; '.join(parts)}"
            )

    return errors
