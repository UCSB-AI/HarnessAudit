"""V3: Verify required == union(ground_truth_tool_paths[role]) for every agent."""

from __future__ import annotations

from pathlib import Path

from scripts.taskgen.yaml_io import read_yaml


def validate_required_invariant(yaml_path: Path) -> list[str]:
    """Check that each role's required set equals the union of its ground truth paths."""
    errors: list[str] = []
    data = read_yaml(yaml_path)
    task_id = data.get("task_id", yaml_path.stem)
    gt_paths = data.get("ground_truth_tool_paths") or {}

    for agent in data.get("agents", []):
        role = agent.get("role", "?")
        tn = agent.get("tool_necessity", {})
        required = set(tn.get("required", []))

        if role not in gt_paths:
            # Hub agent has no ground truth paths — required should be empty
            if required:
                errors.append(
                    f"{task_id} agent={role} has required tools {required} but no ground_truth_tool_paths entry"
                )
            continue

        paths = gt_paths[role]
        union_of_paths = set()
        for path in paths:
            union_of_paths.update(path)

        if required != union_of_paths:
            missing = union_of_paths - required
            extra = required - union_of_paths
            parts = []
            if missing:
                parts.append(f"missing from required: {missing}")
            if extra:
                parts.append(f"extra in required: {extra}")
            errors.append(
                f"{task_id} agent={role} required != union(ground_truth_paths): {'; '.join(parts)}"
            )

    return errors
