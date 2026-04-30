"""Stage 2: Generate fixture/seed data for each new task scenario."""

from __future__ import annotations

import logging
from pathlib import Path

from scripts.taskgen.existing_inventory import DomainInventory
from scripts.taskgen.llm_client import generate_json
from scripts.taskgen.prompt_builder import build_stage_2_prompt
from scripts.taskgen.yaml_io import read_json, write_json

log = logging.getLogger(__name__)


def run_stage_2(
    inventory: DomainInventory,
    scenarios: list[dict],
    staging_dir: Path,
    *,
    dry_run: bool = False,
) -> dict[str, dict]:
    """Generate fixture data for each scenario. Returns {task_id: fixture_entries}."""
    log.info("Stage 2: Generating fixtures for %d scenarios", len(scenarios))

    all_fixtures: dict[str, dict] = {}
    generated_ids: set[str] = set(inventory.entity_ids)

    for i, scenario in enumerate(scenarios):
        task_id = scenario.get("task_id", f"unknown-{i}")
        log.info("  [%d/%d] %s — generating fixtures...", i + 1, len(scenarios), task_id)

        system_prompt, user_prompt = build_stage_2_prompt(
            inventory, [scenario],
        )

        result = generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_output_tokens=8192,
            dry_run=dry_run,
        )

        task_fixtures = result if isinstance(result, dict) else {}
        all_fixtures[task_id] = task_fixtures

        # Extract entity IDs from generated fixtures and accumulate
        from scripts.taskgen.existing_inventory import _extract_ids
        new_ids: set[str] = set()
        _extract_ids(task_fixtures, new_ids)
        generated_ids.update(new_ids)
        log.info("    Extracted %d new entity IDs (total: %d)",
                 len(new_ids), len(generated_ids))

    write_json(staging_dir / "stage_2_fixtures.json", all_fixtures)
    log.info("  Stage 2 complete: fixtures for %d tasks, %d entity IDs",
             len(all_fixtures), len(generated_ids))
    return all_fixtures
