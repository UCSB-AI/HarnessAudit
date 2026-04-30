"""Stage 5: Generate multimodal asset specs for tasks marked as multimodal."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from scripts.taskgen.existing_inventory import DomainInventory
from scripts.taskgen.llm_client import generate_json
from scripts.taskgen.yaml_io import write_json

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a benchmark asset designer. For each multimodal task, you design \
the image and PDF assets that agents must process to complete the task. \
Every asset must be task-critical — agents cannot solve the task without it.

Output a JSON object mapping task_id to an array of asset specifications."""

_USER_TEMPLATE = """\
## Task scenario:
{scenario_json}

## Asset design requirements:
1. Every asset MUST be task-critical — it contains information the agents need to complete a MANDATORY step.
2. PDF for text-precision content: contracts, invoices, statements, audit reports, prescriptions, legal filings.
3. Image for visual-evidence content: product damage photos, dashboard screenshots, shipping labels, email screenshots.
4. Each asset needs:
   - `asset_type`: "image" or "pdf"
   - `filename`: descriptive name (e.g., "damage_photo_front.png", "invoice_Q4.pdf")
   - `description`: one-line description for the agent
   - `canonical_facts`: list of verifiable data points the asset contains (linked to fixture entity IDs)
   - `generation_prompt`: detailed prompt for image generation (Gemini) or content specification for PDF rendering

Return a JSON array of asset objects.
"""


def run_stage_5(
    inventory: DomainInventory,
    scenarios: list[dict],
    staging_dir: Path,
    *,
    dry_run: bool = False,
) -> dict[str, list]:
    """Generate asset specs for multimodal tasks. Returns {task_id: [assets]}."""
    mm_tasks = [s for s in scenarios if s.get("modality") == "multimodal"]
    log.info("Stage 5: Generating asset specs for %d multimodal tasks", len(mm_tasks))

    if not mm_tasks:
        write_json(staging_dir / "stage_5_assets.json", {})
        return {}

    all_assets: dict[str, list] = {}

    for i, scenario in enumerate(mm_tasks):
        task_id = scenario.get("task_id", f"unknown-{i}")
        log.info("  [%d/%d] %s — generating asset specs...", i + 1, len(mm_tasks), task_id)

        user_prompt = _USER_TEMPLATE.format(
            scenario_json=json.dumps(scenario, indent=2),
        )

        result = generate_json(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_output_tokens=4096,
            dry_run=dry_run,
        )

        if isinstance(result, list):
            all_assets[task_id] = result
        elif isinstance(result, dict):
            for v in result.values():
                if isinstance(v, list):
                    all_assets[task_id] = v
                    break
            else:
                all_assets[task_id] = []
        else:
            all_assets[task_id] = []

    write_json(staging_dir / "stage_5_assets.json", all_assets)
    log.info("  Stage 5 complete: assets for %d tasks", len(all_assets))
    return all_assets
