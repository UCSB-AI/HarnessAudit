"""Task input and multimodal asset helpers for framework adapters."""

from __future__ import annotations

import base64
import mimetypes
import shutil
from pathlib import Path
from typing import Any, cast

from multi_agent.schemas.task import TaskConfig


def extract_input_text(initial_input: str | list[dict[str, Any]]) -> str:
    """Extract plain text from a potentially multimodal initial input payload."""
    if isinstance(initial_input, str):
        return initial_input
    parts: list[str] = []
    for msg in initial_input:
        content = msg.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") in ("input_text", "text"):
                    text = str(item.get("text", "")).strip()
                    if text:
                        parts.append(text)
    return "\n".join(parts)


def native_harness_asset_refs(
    task: TaskConfig,
    *,
    asset_dir_name: str = "task_assets",
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for index, asset in enumerate(cast(list[Any], getattr(task, "input_assets", [])), start=1):
        source_path = Path(asset.resolved_path or asset.path)
        target_name = source_path.name
        if target_name in seen_names:
            target_name = f"{index}_{target_name}"
        seen_names.add(target_name)
        refs.append(
            {
                "source_path": str(source_path),
                "target_name": target_name,
                "relative_path": (Path(asset_dir_name) / target_name).as_posix(),
                "asset_type": str(asset.asset_type),
                "description": str(asset.description or "").strip(),
            }
        )

    return refs


def build_native_harness_input(
    task: TaskConfig,
    *,
    asset_dir_name: str = "task_assets",
) -> str:
    """Render task input for native CLI harnesses that consume plain text prompts."""
    modality = cast(str, getattr(task, "modality", "text_only"))
    refs = native_harness_asset_refs(task, asset_dir_name=asset_dir_name)
    if modality != "multimodal" or not refs:
        return task.goal

    lines = [
        task.goal.strip(),
        "",
        "Attached task assets are available in your current workspace:",
    ]
    for ref in refs:
        description = f" — {ref['description']}" if ref["description"] else ""
        lines.append(f"- {ref['relative_path']} ({ref['asset_type']}){description}")
    lines.extend(
        [
            "",
            (
                "Use these local files directly when the task requires reviewing "
                "the attached evidence."
            ),
        ]
    )
    return "\n".join(lines)


def stage_task_input_assets(
    task: TaskConfig,
    workspace: str | Path,
    *,
    asset_dir_name: str = "task_assets",
) -> list[Path]:
    """Copy task-level multimodal assets into a native harness workspace."""
    modality = cast(str, getattr(task, "modality", "text_only"))
    refs = native_harness_asset_refs(task, asset_dir_name=asset_dir_name)
    if modality != "multimodal" or not refs:
        return []

    asset_root = Path(workspace) / asset_dir_name
    asset_root.mkdir(parents=True, exist_ok=True)

    staged_paths: list[Path] = []
    for ref in refs:
        destination = asset_root / ref["target_name"]
        shutil.copy2(ref["source_path"], destination)
        staged_paths.append(destination)
    return staged_paths

def _image_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed and guessed.startswith("image/"):
        return guessed
    suffix = path.suffix.lower()
    fallback = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    return fallback.get(suffix, "image/png")


def _file_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    if path.suffix.lower() == ".pdf":
        return "application/pdf"
    return "application/octet-stream"


def build_task_input(task: TaskConfig) -> str | list[dict[str, Any]]:
    """Build canonical initial input payload from TaskConfig."""
    modality = cast(str, getattr(task, "modality", "text_only"))
    input_assets = cast(list[Any], getattr(task, "input_assets", []))

    if modality != "multimodal" or not input_assets:
        return task.goal

    content: list[dict[str, Any]] = [{"type": "input_text", "text": task.goal}]
    for asset in input_assets:
        asset_path = Path(asset.resolved_path or asset.path)
        asset_bytes = asset_path.read_bytes()
        if asset.description:
            content.append(
                {
                    "type": "input_text",
                    "text": (
                        f"Use attached {asset.asset_type} "
                        f"'{asset_path.name}': {asset.description}"
                    ),
                }
            )

        if asset.asset_type == "image":
            content.append(
                {
                    "type": "input_image",
                    "image_url": (
                        f"data:{_image_mime_type(asset_path)};base64,"
                        f"{base64.b64encode(asset_bytes).decode('ascii')}"
                    ),
                    "detail": asset.detail,
                }
            )
            continue

        content.append(
            {
                "type": "input_file",
                "file_data": (
                    f"data:{_file_mime_type(asset_path)};base64,"
                    f"{base64.b64encode(asset_bytes).decode('ascii')}"
                ),
                "filename": asset_path.name,
            }
        )

    return [{"role": "user", "content": content}]
