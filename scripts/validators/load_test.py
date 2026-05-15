"""V8: Integration test — load_task_with_tools() must succeed."""

from __future__ import annotations

from pathlib import Path


def validate_load(yaml_path: Path) -> list[str]:
    """Attempt to load a task via the full loader pipeline."""
    errors: list[str] = []
    try:
        from multi_agent.loader import load_task_with_tools, resolve_task_path
        from pathlib import Path as P

        multi_agent_root = P(__file__).resolve().parents[2] / "multi_agent"
        resolved = resolve_task_path(
            str(yaml_path), project_root=multi_agent_root, cwd=P.cwd(),
        )
        task, catalog = load_task_with_tools(resolved)
    except Exception as exc:
        errors.append(f"load_task_with_tools() failed: {exc}")

    return errors
