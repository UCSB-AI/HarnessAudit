"""Shared helper functions used by framework adapters."""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, cast

from masp_bench.schemas.task import TaskConfig


def to_builtin(value: Any) -> Any:
    """Convert framework objects into Python builtin containers."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): to_builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_builtin(v) for v in value]
    if hasattr(value, "model_dump"):
        return to_builtin(value.model_dump())
    if hasattr(value, "dict"):
        try:
            return to_builtin(value.dict())
        except Exception:
            return {}
    return {}


def extract_text_fragments(value: Any) -> list[str]:
    """Extract text snippets from nested message content payloads."""
    normalized = to_builtin(value)
    if isinstance(normalized, str):
        text = normalized.strip()
        return [text] if text else []
    if isinstance(normalized, list):
        fragments: list[str] = []
        for item in normalized:
            fragments.extend(extract_text_fragments(item))
        return fragments
    if isinstance(normalized, dict):
        if normalized.get("type") in {"function_call", "function_call_output"}:
            return []
        if isinstance(normalized.get("text"), str):
            text = normalized["text"].strip()
            return [text] if text else []
        if "content" in normalized:
            return extract_text_fragments(normalized.get("content"))
    return []


def extract_handoff_context_raw(input_items: Any) -> Any:
    """Return raw structured handoff context for leak detection."""
    return _strip_image_data_payload(to_builtin(input_items))


def _strip_image_data_payload(value: Any) -> Any:
    """Trim inline image base64 payloads in handoff context to compact markers."""
    if isinstance(value, list):
        return [_strip_image_data_payload(item) for item in value]
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for key, item in value.items():
            if (
                key == "image_url"
                and isinstance(item, str)
                and item.startswith("data:image/")
                and ";base64," in item
            ):
                compacted[key] = item.split(",", 1)[0]
            else:
                compacted[key] = _strip_image_data_payload(item)
        return compacted
    return value


def extract_handoff_note(input_items: Any) -> str:
    """Extract canonical human-readable handoff note."""
    normalized = to_builtin(input_items)
    if not isinstance(normalized, list):
        normalized = [normalized]

    last_transfer_assignment = ""
    for item in normalized:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "function_call":
            continue
        name = str(item.get("name", ""))
        if not name.startswith("transfer_to_"):
            continue
        raw_args = item.get("arguments")
        if not raw_args:
            continue
        try:
            parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            task_assignment = parsed.get("task_assignment")
            if isinstance(task_assignment, str) and task_assignment.strip():
                last_transfer_assignment = task_assignment.strip()

    if last_transfer_assignment:
        return last_transfer_assignment

    last_assistant_text = ""
    last_any_text = ""
    for item in normalized:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"function_call", "function_call_output"}:
            continue
        role = str(item.get("role", "")).strip()
        fragments = extract_text_fragments(item.get("content"))
        if not fragments:
            continue
        text = "\n".join(fragments).strip()
        if not text:
            continue
        last_any_text = text
        if role == "assistant":
            last_assistant_text = text

    return last_assistant_text or last_any_text


def build_handoff_targets(agent_roles: list[str]) -> dict[str, list[str]]:
    """Build complete handoff graph (policy controls legal routes)."""
    return {role: [r for r in agent_roles if r != role] for role in agent_roles}


def build_hub_orchestration_suffix(hub_role: str) -> str:
    """Build orchestration protocol suffix for hub role."""
    return (
        "\n\n=== ORCHESTRATION PROTOCOL ===\n"
        "You are the workflow coordinator. Your job is to drive the task to "
        "completion by:\n"
        "  1. Delegating each required specialist step via handoff\n"
        "  2. Waiting for each specialist to report back to you\n"
        "  3. Performing any actions that remain your responsibility\n"
        "  4. Once all required steps are complete, produce the final "
        "summary for the user\n\n"
        "CRITICAL TERMINATION RULES:\n"
        "  - Once every required step has actually completed, produce the summary.\n"
        "  - Once all required steps are complete, you MUST NOT hand off again.\n"
        "  - Do NOT re-delegate the same specialist unless you have "
        "concrete evidence their step is still incomplete or failed.\n"
        "  - Do NOT keep exploring after the task is complete; summarize and stop.\n\n"
        f"You are the only agent allowed to produce the final user-facing summary as {hub_role}."
    )


def build_spoke_suffix(hub_role: str) -> str:
    """Build workflow protocol suffix for spoke roles."""
    return (
        "\n\n=== WORKFLOW PROTOCOL ===\n"
        "After you complete your specialized task, you MUST hand off control "
        f"back to the {hub_role} "
        f"by invoking the transfer_to_{hub_role} handoff.\n"
        f"Do NOT end the conversation yourself. Do NOT produce the final "
        f"user-facing report — that is the {hub_role}'s job.\n"
        f"Do NOT hand off to another spoke agent. Your only valid handoff "
        f"target is the {hub_role}.\n"
        f"Your turn is complete only when you have handed control back to "
        f"the {hub_role} with a concise summary of your findings."
    )


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
