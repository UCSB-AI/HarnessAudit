"""V9: Detect duplicate tasks via goal text similarity."""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

from scripts.taskgen.existing_inventory import DomainInventory
from scripts.taskgen.yaml_io import read_yaml

SIMILARITY_THRESHOLD = 0.85


def validate_dedup(
    yaml_path: Path,
    inventory: DomainInventory,
    *,
    batch_goals: dict[str, str] | None = None,
) -> list[str]:
    """Flag tasks whose goals are too similar to existing or batch-peer tasks.

    Uses SequenceMatcher as a fast proxy. For production use, replace with
    embedding-based similarity if available.

    Args:
        batch_goals: ``{task_id: goal}`` for other tasks in the same
            generation batch.  Enables intra-batch duplicate detection.
    """
    errors: list[str] = []
    data = read_yaml(yaml_path)
    task_id = data.get("task_id", yaml_path.stem)
    new_goal = data.get("goal", "")

    if not new_goal:
        errors.append(f"{task_id} has empty goal")
        return errors

    # Check against existing (already-deployed) tasks
    for existing_task in inventory.tasks:
        if existing_task.task_id == task_id:
            continue
        ratio = SequenceMatcher(None, new_goal, existing_task.goal).ratio()
        if ratio >= SIMILARITY_THRESHOLD:
            errors.append(
                f"{task_id} goal is {ratio:.0%} similar to {existing_task.task_id} — likely duplicate"
            )

    # Check against batch peers (only compare against earlier IDs to avoid
    # double-reporting A↔B and B↔A)
    if batch_goals:
        for peer_id, peer_goal in batch_goals.items():
            if peer_id >= task_id:
                continue
            ratio = SequenceMatcher(None, new_goal, peer_goal).ratio()
            if ratio >= SIMILARITY_THRESHOLD:
                errors.append(
                    f"{task_id} goal is {ratio:.0%} similar to batch peer "
                    f"{peer_id} — likely intra-batch duplicate"
                )

    return errors
