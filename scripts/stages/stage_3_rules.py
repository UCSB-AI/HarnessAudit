"""Stage 3: Generate access_rules, tool_necessity, and ground_truth_tool_paths."""

from __future__ import annotations

import logging
import re
from difflib import get_close_matches
from pathlib import Path
from typing import Any

from scripts.taskgen.existing_inventory import DomainInventory, _extract_ids
from scripts.taskgen.llm_client import generate_json
from scripts.taskgen.prompt_builder import build_stage_3_prompt
from scripts.taskgen.yaml_io import write_json

log = logging.getLogger(__name__)

# Same regex used by the V5 fixture_coherence validator.
_ENTITY_ID_RE = re.compile(r"^[A-Z]{1,10}[-_][A-Za-z0-9_-]+$")


# ------------------------------------------------------------------
# Post-generation repair
# ------------------------------------------------------------------

def _collect_fixture_ids(
    task_fixtures: dict[str, Any],
    inventory: DomainInventory,
) -> set[str]:
    """Collect all valid entity IDs from task fixtures + domain fixtures."""
    ids: set[str] = set(inventory.entity_ids)
    _extract_ids(task_fixtures, ids)
    return ids


def _fuzzy_find(bad_id: str, valid_ids: set[str]) -> str | None:
    """Return the closest valid ID, or *None* if nothing is close enough."""
    matches = get_close_matches(bad_id, list(valid_ids), n=1, cutoff=0.75)
    return matches[0] if matches else None


def _repair_allowed_args(
    rules: dict[str, Any],
    valid_ids: set[str],
) -> int:
    """Validate and repair allowed_args entity IDs in-place. Returns fix count."""
    fixed = 0
    for rule in rules.get("access_rules", []):
        if rule.get("access_type") != "operation.resource":
            continue
        match = rule.get("match") or {}
        aa = match.get("allowed_args")
        if not aa or not isinstance(aa, dict):
            continue
        for arg_name, arg_value in list(aa.items()):
            val = str(arg_value)
            # Only repair values that look like entity IDs
            if not _ENTITY_ID_RE.match(val):
                continue
            if val in valid_ids:
                continue
            # Try fuzzy match
            replacement = _fuzzy_find(val, valid_ids)
            if replacement:
                log.debug(
                    "    repair: %s=%s → %s", arg_name, val, replacement,
                )
                aa[arg_name] = replacement
            else:
                log.debug("    drop: %s=%s (no close match)", arg_name, val)
                del aa[arg_name]
            fixed += 1
    return fixed


# ------------------------------------------------------------------
# Stage entry point
# ------------------------------------------------------------------

def run_stage_3(
    inventory: DomainInventory,
    scenarios: list[dict],
    fixtures: dict[str, dict],
    staging_dir: Path,
    *,
    dry_run: bool = False,
) -> dict[str, dict]:
    """Generate rules for each scenario. Returns {task_id: {access_rules, tool_necessity, ground_truth_tool_paths}}."""
    log.info("Stage 3: Generating rules for %d scenarios", len(scenarios))

    all_rules: dict[str, dict] = {}

    for i, scenario in enumerate(scenarios):
        task_id = scenario.get("task_id", f"unknown-{i}")
        task_fixtures = fixtures.get(task_id, {})
        log.info("  [%d/%d] %s — generating rules...", i + 1, len(scenarios), task_id)

        system_prompt, user_prompt = build_stage_3_prompt(
            inventory, scenario, task_fixtures,
        )

        result = generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_output_tokens=8192,
            dry_run=dry_run,
        )

        task_rules = result if isinstance(result, dict) else {}

        # Post-gen repair: fix hallucinated entity IDs
        if task_rules and not dry_run:
            valid_ids = _collect_fixture_ids(task_fixtures, inventory)
            n_fixed = _repair_allowed_args(task_rules, valid_ids)
            if n_fixed:
                log.info("    repaired %d allowed_args ID(s) for %s", n_fixed, task_id)

        all_rules[task_id] = task_rules

    write_json(staging_dir / "stage_3_rules.json", all_rules)
    log.info("  Stage 3 complete: rules for %d tasks", len(all_rules))
    return all_rules
