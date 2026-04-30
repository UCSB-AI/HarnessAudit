"""V6: Verify all V-ID data_class values are registered in the recognizer."""

from __future__ import annotations

from pathlib import Path

from scripts.taskgen.existing_inventory import DomainInventory
from scripts.taskgen.yaml_io import read_yaml


def validate_recognizer(yaml_path: Path, inventory: DomainInventory) -> list[str]:
    """Check all data_class values in V-ID rules exist in the recognizer registry."""
    errors: list[str] = []
    data = read_yaml(yaml_path)
    task_id = data.get("task_id", yaml_path.stem)
    registered = set(inventory.data_classes)

    for rule in data.get("access_rules", []):
        if rule.get("access_type") != "information.data_leak":
            continue
        match = rule.get("match", {})
        data_class = match.get("data_class", "")
        if data_class and data_class not in registered:
            errors.append(
                f"{task_id} V-ID rule data_class='{data_class}' not registered "
                f"in recognizer for domain={inventory.config.recognizer_domain}"
            )

    return errors
