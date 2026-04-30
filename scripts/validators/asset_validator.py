"""V10: Validate multimodal assets exist and paths resolve."""

from __future__ import annotations

from pathlib import Path

from scripts.taskgen.yaml_io import read_yaml


def validate_assets(yaml_path: Path) -> list[str]:
    """Check multimodal task assets exist at referenced paths."""
    errors: list[str] = []
    data = read_yaml(yaml_path)
    task_id = data.get("task_id", yaml_path.stem)
    modality = data.get("modality", "text")

    input_assets = data.get("input_assets", [])

    if modality == "multimodal" and not input_assets:
        errors.append(f"{task_id} modality=multimodal but no input_assets defined")
        return errors

    for asset in input_assets:
        raw_path = asset.get("path", "")
        if not raw_path:
            errors.append(f"{task_id} asset has empty path")
            continue

        resolved = Path(raw_path)
        if not resolved.is_absolute():
            resolved = yaml_path.parent / resolved

        if not resolved.exists():
            errors.append(f"{task_id} asset not found: {raw_path} (resolved: {resolved})")

        asset_type = asset.get("asset_type", "")
        if asset_type not in ("image", "pdf"):
            errors.append(f"{task_id} asset has invalid type: {asset_type}")

    return errors
