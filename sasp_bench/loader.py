"""SA task loader — wraps masp_bench.loader with SA-specific paths."""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from masp_bench.loader import (
    load_task,
    synthesize_allowed_tool_rules,
    synthesize_communication_rules,
)
from masp_bench.schemas.access_rules import AccessMatch, AccessRule
from masp_bench.schemas.task import TaskConfig, ToolCatalog


def sa_package_root() -> Path:
    """Root of the sasp_bench package."""
    return Path(__file__).resolve().parent


def sa_project_root() -> Path:
    """Package root — sasp_bench/ itself (contains tasks/, results/, traces/)."""
    return sa_package_root()


def sa_tools_dir() -> Path:
    return sa_package_root() / "tools"


def sa_fixtures_dir() -> Path:
    return sa_package_root() / "fixtures"


def resolve_sa_task_path(
    spec: str | Path,
    *,
    project_root: Path | None = None,
    cwd: Path | None = None,
) -> Path:
    """Resolve SA task path — searches sasp_bench/tasks/."""
    cwd = cwd or Path.cwd()
    project_root = project_root or sa_project_root()
    raw = Path(spec)

    candidates: list[Path] = []

    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(cwd / raw)
        candidates.append(project_root / raw)

    # Search tasks/ subdirectories inside sasp_bench/
    sa_tasks_dir = project_root / "tasks"
    name = raw.name
    if name.endswith((".yaml", ".yml")) and sa_tasks_dir.is_dir():
        for domain_dir in sorted(sa_tasks_dir.iterdir()):
            if domain_dir.is_dir():
                for match in domain_dir.rglob(name):
                    candidates.append(match)

    seen: set[str] = set()
    for c in candidates:
        key = os.path.normpath(str(c))
        if key in seen:
            continue
        seen.add(key)
        if c.is_file():
            return c.resolve()

    tried = ", ".join(str(c) for c in candidates)
    raise FileNotFoundError(f"SA task file not found: {spec!r}. Tried: {tried}")


def load_sa_tool_catalog(domain: str) -> ToolCatalog:
    """Load tool catalog from sasp_bench/tools/ with SA fixtures."""
    tools_dir = sa_tools_dir()
    path = tools_dir / f"{domain}.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)
    catalog = ToolCatalog(**data)

    # Load fixture data from SA fixtures directory
    fixtures_dir = sa_fixtures_dir() / domain
    for tool in catalog.tools:
        if tool.fixture and tool.fixture_key:
            fixture_path = fixtures_dir / tool.fixture
            if fixture_path.exists():
                with open(fixture_path) as f:
                    tool.fixture_data = json.load(f)

    return catalog


def load_sa_task_with_tools(
    task_path: str | Path,
    *,
    project_root: Path | None = None,
    cwd: Path | None = None,
) -> tuple[TaskConfig, ToolCatalog]:
    """Load an SA task and its tool catalog, then synthesize rules."""
    path = resolve_sa_task_path(task_path, project_root=project_root, cwd=cwd)
    task = load_task(path)
    catalog = load_sa_tool_catalog(task.domain)
    synthesize_allowed_tool_rules(task, catalog)
    synthesize_communication_rules(task)  # no-op for single agent
    return task, catalog
