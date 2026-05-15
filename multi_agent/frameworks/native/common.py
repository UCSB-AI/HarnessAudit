"""Shared helpers for native CLI harness runtimes."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _process_output_text(*parts: object) -> str:
    """Normalize subprocess stdout/stderr snippets for error messages."""

    lines: list[str] = []
    for part in parts:
        if isinstance(part, bytes):
            text = part.decode(errors="replace")
        elif isinstance(part, str):
            text = part
        else:
            continue
        text = text.strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def _requested_auth_mode(env_name: str) -> str:
    requested = os.environ.get(env_name, "auto").strip().lower()
    if requested not in {"", "auto", "api_key", "cli_login"}:
        log.warning(
            "Ignoring unsupported %s=%r; expected auto, api_key, or cli_login.",
            env_name,
            requested,
        )
        return "auto"
    return requested or "auto"


def _append_comma_flag_values(
    command: list[str],
    flag: str,
    values: list[str] | tuple[str, ...],
) -> list[str]:
    adjusted = list(command)
    for idx, value in enumerate(adjusted[:-1]):
        if value != flag:
            continue

        existing = [
            item.strip()
            for item in adjusted[idx + 1].split(",")
            if item.strip()
        ]
        for item in values:
            if item not in existing:
                existing.append(item)
        adjusted[idx + 1] = ",".join(existing)
        return adjusted

    adjusted.extend([flag, ",".join(values)])
    return adjusted

def _safe_temp_fragment(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value)
    return safe[:80] or "run"


def _mk_isolation_root(*, prefix: str, root_env: str | None = None) -> Path:
    parent: Path | None = None
    if root_env:
        raw_parent = os.environ.get(root_env, "").strip()
        if raw_parent:
            parent = Path(raw_parent)
            parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent) if parent else None))


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _load_text_or_empty(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
