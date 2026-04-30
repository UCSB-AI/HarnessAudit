"""V1: Pydantic schema validation for generated task YAMLs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.taskgen.yaml_io import read_yaml


def validate_schema(yaml_path: Path) -> list[str]:
    """Validate a task YAML against TaskConfig schema. Returns list of errors."""
    errors: list[str] = []
    try:
        data = read_yaml(yaml_path)
    except Exception as exc:
        return [f"YAML parse error: {exc}"]

    if not isinstance(data, dict):
        return ["YAML root is not a dict"]

    try:
        from masp_bench.schemas.task import TaskConfig
        TaskConfig(**data)
    except Exception as exc:
        errors.append(f"Schema validation failed: {exc}")

    return errors
