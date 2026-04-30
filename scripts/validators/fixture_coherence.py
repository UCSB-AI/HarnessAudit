"""V5: Cross-reference fixture entity IDs against access_rules and checkpoints."""

from __future__ import annotations

import re
from pathlib import Path

from scripts.taskgen.existing_inventory import DomainInventory
from scripts.taskgen.yaml_io import read_yaml

# Arg names that hold type/filter values rather than entity IDs.
# These are validated by semantics, not by fixture lookup.
_NON_ID_ARGS = {
    "record_type", "query", "hashtag", "issue_tag",
    "dashboard_name", "report_type", "status", "category",
    "action", "template_id", "message_id",
    "authorized_payment_ref", "serial_number",
}

# Pattern for values that look like entity IDs (PREFIX-SUFFIX)
_ENTITY_ID_RE = re.compile(r"^[A-Z]{1,10}[-_][A-Za-z0-9_-]+$")


def validate_fixture_coherence(
    yaml_path: Path,
    inventory: DomainInventory,
    *,
    extra_ids: set[str] | None = None,
) -> list[str]:
    """Check entity IDs in allowed_args exist in fixture seed data.

    Skips args that are clearly non-ID values (type filters, search queries,
    dashboard names, etc.).

    Args:
        extra_ids: Additional entity IDs to accept (e.g. from stage_2
            generated fixtures not yet merged to disk).
    """
    errors: list[str] = []
    data = read_yaml(yaml_path)
    task_id = data.get("task_id", yaml_path.stem)
    all_ids = inventory.entity_ids | (extra_ids or set())

    for rule in data.get("access_rules", []):
        if rule.get("access_type") != "operation.resource":
            continue
        match = rule.get("match", {})
        allowed_args = match.get("allowed_args", {})
        for arg_name, arg_value in allowed_args.items():
            if not arg_value:
                continue
            # Skip known non-ID argument types
            if arg_name in _NON_ID_ARGS:
                continue
            # Normalise to a list of values (allowed_args can be str or list)
            values = arg_value if isinstance(arg_value, list) else [arg_value]
            for v in values:
                val = str(v)
                if not val:
                    continue
                # Skip values that don't look like entity IDs
                if not _ENTITY_ID_RE.match(val):
                    continue
                if val not in all_ids:
                    errors.append(
                        f"{task_id} V-OR rule role={rule.get('role')} "
                        f"allowed_args.{arg_name}={val} not found in fixtures"
                    )

    return errors
