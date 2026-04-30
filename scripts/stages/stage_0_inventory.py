"""Stage 0: Build domain inventory for context-aware generation."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from scripts.taskgen.domain_config import DomainConfig
from scripts.taskgen.existing_inventory import DomainInventory, load_domain_inventory
from scripts.taskgen.yaml_io import write_json

log = logging.getLogger(__name__)


def run_stage_0(config: DomainConfig, staging_dir: Path) -> DomainInventory:
    """Build and save domain inventory."""
    log.info("Stage 0: Loading inventory for domain=%s", config.domain)

    inventory = load_domain_inventory(config)

    # Save inventory summary to staging
    summary = {
        "domain": config.domain,
        "existing_tasks": len(inventory.tasks),
        "existing_task_ids": sorted(inventory.existing_task_ids),
        "tool_count": len(inventory.tools),
        "tool_names": sorted(inventory.tool_names),
        "resource_tools": sorted(inventory.resource_tool_names),
        "data_classes": inventory.data_classes,
        "bank_tables": inventory.bank_tables,
        "entity_id_count": len(inventory.entity_ids),
        "categories": sorted(inventory.existing_categories),
        "exemplar_tasks": list(inventory.exemplar_yamls.keys()),
        "existing_goals": [
            {"task_id": t.task_id, "goal_preview": t.goal[:120]}
            for t in inventory.tasks
        ],
    }
    write_json(staging_dir / "stage_0_inventory.json", summary)

    log.info(
        "  %d existing tasks, %d tools (%d resource), %d data_classes, %d bank tables, %d entity IDs",
        len(inventory.tasks),
        len(inventory.tools),
        len(inventory.resource_tool_names),
        len(inventory.data_classes),
        len(inventory.bank_tables),
        len(inventory.entity_ids),
    )

    return inventory
