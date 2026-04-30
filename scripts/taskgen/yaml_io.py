"""YAML read/write utilities for the task generation pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def read_yaml(path: Path | str) -> Any:
    """Read a YAML file and return parsed content."""
    with open(path) as f:
        return yaml.safe_load(f)


def write_yaml(path: Path | str, data: Any) -> None:
    """Write data to a YAML file with consistent formatting."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(
            data,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )


def read_json(path: Path | str) -> Any:
    """Read a JSON file and return parsed content."""
    with open(path) as f:
        return json.load(f)


def write_json(path: Path | str, data: Any) -> None:
    """Write data to a JSON file with consistent formatting."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def merge_seed_json(path: Path, new_entries: dict[str, Any] | list[Any]) -> None:
    """Merge new entries into an existing seed JSON file.

    For dict-based files: merges keys. If a top-level key already exists
    and both the old and new values are dicts, merges them recursively
    (one level deep — enough for wrapper keys like ``"cases"``,
    ``"customers"``, ``"payment_methods"``).  True entity-ID collisions
    at the leaf level are silently skipped (existing data wins).

    For list-based files: appends new entries.
    """
    existing = read_json(path) if path.exists() else ([] if isinstance(new_entries, list) else {})

    if isinstance(existing, dict) and isinstance(new_entries, dict):
        for key, new_val in new_entries.items():
            if key not in existing:
                existing[key] = new_val
            elif isinstance(existing[key], dict) and isinstance(new_val, dict):
                # Merge one level deeper (e.g. "cases": {id: {...}} )
                for sub_key, sub_val in new_val.items():
                    if sub_key not in existing[key]:
                        existing[key][sub_key] = sub_val
                    # else: existing entity wins — skip duplicate
            elif isinstance(existing[key], list) and isinstance(new_val, list):
                existing[key].extend(new_val)
            # else: existing scalar wins — skip
    elif isinstance(existing, list) and isinstance(new_entries, list):
        existing.extend(new_entries)
    else:
        raise TypeError(
            f"Cannot merge {type(new_entries).__name__} into "
            f"{type(existing).__name__} in {path}"
        )

    write_json(path, existing)
