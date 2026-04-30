"""Run all validators on a task YAML and produce a validation report."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from scripts.taskgen.existing_inventory import DomainInventory
from scripts.validators.schema_validator import validate_schema
from scripts.validators.tool_coverage import validate_tool_coverage
from scripts.validators.required_invariant import validate_required_invariant
from scripts.validators.tool_partition import validate_tool_partition
from scripts.validators.fixture_coherence import validate_fixture_coherence
from scripts.validators.recognizer_validator import validate_recognizer
from scripts.validators.sql_validator import validate_sql
from scripts.validators.load_test import validate_load
from scripts.validators.dedup_validator import validate_dedup
from scripts.validators.asset_validator import validate_assets

log = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    task_id: str
    yaml_path: str
    errors: dict[str, list[str]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not any(self.errors.values())

    @property
    def total_errors(self) -> int:
        return sum(len(v) for v in self.errors.values())

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "yaml_path": self.yaml_path,
            "passed": self.passed,
            "total_errors": self.total_errors,
            "errors": {k: v for k, v in self.errors.items() if v},
        }


def validate_task(
    yaml_path: Path,
    inventory: DomainInventory,
    *,
    skip_load_test: bool = False,
    extra_ids: set[str] | None = None,
    batch_goals: dict[str, str] | None = None,
) -> ValidationResult:
    """Run all validators on a single task YAML."""
    from scripts.taskgen.yaml_io import read_yaml

    data = read_yaml(yaml_path)
    task_id = data.get("task_id", yaml_path.stem) if isinstance(data, dict) else yaml_path.stem

    result = ValidationResult(task_id=task_id, yaml_path=str(yaml_path))

    validators = [
        ("V1_schema", lambda: validate_schema(yaml_path)),
        ("V2_tool_coverage", lambda: validate_tool_coverage(yaml_path, inventory)),
        ("V3_required_invariant", lambda: validate_required_invariant(yaml_path)),
        ("V4_tool_partition", lambda: validate_tool_partition(yaml_path, inventory)),
        ("V5_fixture_coherence", lambda: validate_fixture_coherence(
            yaml_path, inventory, extra_ids=extra_ids)),
        ("V6_recognizer", lambda: validate_recognizer(yaml_path, inventory)),
        ("V7_sql", lambda: validate_sql(yaml_path, inventory)),
        ("V9_dedup", lambda: validate_dedup(
            yaml_path, inventory, batch_goals=batch_goals)),
        ("V10_assets", lambda: validate_assets(yaml_path)),
    ]

    if not skip_load_test:
        validators.append(
            ("V8_load_test", lambda: validate_load(yaml_path)),
        )

    for name, validator_fn in validators:
        try:
            errors = validator_fn()
            if errors:
                result.errors[name] = errors
        except Exception as exc:
            result.errors[name] = [f"Validator crashed: {exc}"]
            log.warning("Validator %s crashed on %s: %s", name, yaml_path, exc)

    return result


def validate_batch(
    yaml_paths: list[Path],
    inventory: DomainInventory,
    *,
    skip_load_test: bool = False,
    extra_ids: set[str] | None = None,
) -> list[ValidationResult]:
    """Run all validators on a batch of task YAMLs."""
    # Build batch_goals for intra-batch dedup detection
    from scripts.taskgen.yaml_io import read_yaml
    batch_goals: dict[str, str] = {}
    for path in yaml_paths:
        data = read_yaml(path)
        if isinstance(data, dict):
            tid = data.get("task_id", path.stem)
            batch_goals[tid] = data.get("goal", "")

    results = []
    for path in yaml_paths:
        result = validate_task(
            path, inventory,
            skip_load_test=skip_load_test,
            extra_ids=extra_ids,
            batch_goals=batch_goals,
        )
        status = "PASS" if result.passed else f"FAIL ({result.total_errors} errors)"
        log.info("  %s: %s", result.task_id, status)
        results.append(result)
    return results
