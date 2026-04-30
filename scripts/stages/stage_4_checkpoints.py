"""Stage 4: Generate completion_checkpoints for each task."""

from __future__ import annotations

import logging
from pathlib import Path

from scripts.taskgen.existing_inventory import DomainInventory
from scripts.taskgen.llm_client import generate_json
from scripts.taskgen.prompt_builder import build_stage_4_prompt
from scripts.taskgen.yaml_io import write_json

log = logging.getLogger(__name__)


def run_stage_4(
    inventory: DomainInventory,
    scenarios: list[dict],
    rules: dict[str, dict],
    staging_dir: Path,
    *,
    dry_run: bool = False,
) -> dict[str, list]:
    """Generate checkpoints for each scenario. Returns {task_id: [checkpoints]}."""
    log.info("Stage 4: Generating checkpoints for %d scenarios", len(scenarios))

    all_checkpoints: dict[str, list] = {}

    for i, scenario in enumerate(scenarios):
        task_id = scenario.get("task_id", f"unknown-{i}")
        task_rules = rules.get(task_id, {})
        log.info("  [%d/%d] %s — generating checkpoints...", i + 1, len(scenarios), task_id)

        system_prompt, user_prompt = build_stage_4_prompt(
            inventory, scenario, task_rules,
        )

        result = generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_output_tokens=4096,
            dry_run=dry_run,
        )

        if isinstance(result, list):
            all_checkpoints[task_id] = result
        elif isinstance(result, dict):
            # Unwrap {"checkpoints": [...]}
            for v in result.values():
                if isinstance(v, list):
                    all_checkpoints[task_id] = v
                    break
            else:
                all_checkpoints[task_id] = []
        else:
            all_checkpoints[task_id] = []

    write_json(staging_dir / "stage_4_checkpoints.json", all_checkpoints)
    log.info("  Stage 4 complete: checkpoints for %d tasks", len(all_checkpoints))
    return all_checkpoints
