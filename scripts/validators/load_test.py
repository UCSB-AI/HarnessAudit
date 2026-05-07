"""V8: Integration test — load_task_with_tools() must succeed."""

from __future__ import annotations

from pathlib import Path


def validate_load(yaml_path: Path) -> list[str]:
    """Attempt to load a task via the full loader pipeline."""
    errors: list[str] = []
    try:
        from masp.loader import load_task_with_tools, resolve_task_path
        from pathlib import Path as P

        masp_root = P(__file__).resolve().parents[2] / "masp"
        resolved = resolve_task_path(
            str(yaml_path), project_root=masp_root, cwd=P.cwd(),
        )
        task, catalog = load_task_with_tools(resolved)
    except Exception as exc:
        errors.append(f"load_task_with_tools() failed: {exc}")

    return errors
