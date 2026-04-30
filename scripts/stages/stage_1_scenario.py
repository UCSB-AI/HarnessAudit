"""Stage 1: Generate task scenario skeletons via LLM."""

from __future__ import annotations

import logging
import math
from pathlib import Path

from scripts.taskgen.existing_inventory import DomainInventory
from scripts.taskgen.llm_client import generate_json_list
from scripts.taskgen.prompt_builder import build_stage_1_prompt
from scripts.taskgen.yaml_io import write_json

log = logging.getLogger(__name__)

OVER_GENERATION_FACTOR = 1.5


BATCH_SIZE = 10  # scenarios per LLM call


def run_stage_1(
    inventory: DomainInventory,
    staging_dir: Path,
    *,
    target_count: int | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Generate scenario skeletons in batches and save to staging."""
    target = target_count or inventory.config.target_new
    generate_count = math.ceil(target * OVER_GENERATION_FACTOR)
    mm_count = math.ceil(
        inventory.config.target_multimodal_new * OVER_GENERATION_FACTOR
    )

    log.info(
        "Stage 1: Generating %d scenario candidates (target %d accepted, %d multimodal) for domain=%s",
        generate_count, target, mm_count, inventory.config.domain,
    )

    all_scenarios: list[dict] = []
    remaining = generate_count
    remaining_mm = mm_count

    # Clear stale stage_1 file so prev_scenarios_ctx starts fresh for this run
    stage_1_path = staging_dir / "stage_1_scenarios.json"
    if stage_1_path.exists():
        stage_1_path.unlink()

    batch_num = 0
    while remaining > 0:
        batch_num += 1
        batch_size = min(BATCH_SIZE, remaining)
        batch_mm = min(
            math.ceil(remaining_mm * batch_size / max(remaining, 1)),
            batch_size,
        )

        log.info("  Batch %d: requesting %d scenarios (%d multimodal)...",
                 batch_num, batch_size, batch_mm)

        system_prompt, user_prompt = build_stage_1_prompt(
            inventory, batch_size, batch_mm,
            staging_dir=staging_dir,
        )

        batch = generate_json_list(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_output_tokens=16384,
            dry_run=dry_run,
        )

        all_scenarios.extend(batch)
        remaining -= batch_size
        remaining_mm -= batch_mm

        log.info("  Batch %d: got %d scenarios (total so far: %d)",
                 batch_num, len(batch), len(all_scenarios))

        # Write incrementally so the NEXT batch's prompt sees all prior
        # scenarios via prev_scenarios_ctx — prevents cross-batch duplication
        write_json(stage_1_path, all_scenarios)

    # Assign globally sequential task IDs (overwrite LLM-assigned IDs to avoid collisions)
    for i, scenario in enumerate(all_scenarios):
        scenario["task_id"] = inventory.config.next_task_id(i)

    write_json(stage_1_path, all_scenarios)

    log.info("  Generated %d scenarios total", len(all_scenarios))
    for s in all_scenarios:
        mod = s.get("modality", "text")
        log.info("    %s [%s] %s — %s",
                 s.get("task_id", "?"), mod,
                 s.get("category", "?"),
                 (s.get("goal", "") or "")[:80])

    return all_scenarios
