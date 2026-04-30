"""Stage 6: Scan for new data_class values not yet in the recognizer (deterministic)."""

from __future__ import annotations

import logging
from pathlib import Path

from scripts.taskgen.existing_inventory import DomainInventory
from scripts.taskgen.yaml_io import write_json

log = logging.getLogger(__name__)


def run_stage_6(
    inventory: DomainInventory,
    rules: dict[str, dict],
    staging_dir: Path,
) -> list[str]:
    """Scan rules for new data_classes not in recognizer. Returns list of missing classes."""
    registered = set(inventory.data_classes)
    missing: set[str] = set()

    for task_id, task_rules in rules.items():
        for rule in task_rules.get("access_rules", []):
            if rule.get("access_type") != "information.data_leak":
                continue
            match = rule.get("match", {})
            dc = match.get("data_class", "")
            if dc and dc not in registered:
                missing.add(dc)

    result = sorted(missing)

    write_json(staging_dir / "stage_6_recognizer.json", {
        "missing_data_classes": result,
        "registered_count": len(registered),
        "status": "all_covered" if not result else "needs_review",
    })

    if result:
        log.warning(
            "Stage 6: %d new data_classes need recognizer patterns: %s",
            len(result), result,
        )
    else:
        log.info("Stage 6: All data_classes already registered in recognizer.")

    return result
