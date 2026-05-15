"""SA task loader — wraps multi_agent.loader with SA-specific paths."""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from multi_agent.loader import (
    load_task,
    synthesize_useful_tool_rules,
    synthesize_communication_rules,
)
from multi_agent.schemas.access_rules import AccessMatch, AccessRule
from multi_agent.schemas.task import TaskConfig, ToolCatalog

_FIXTURE_DOMAIN_ALIASES = {
    "office": "office_enterprise_management",
}


def sa_package_root() -> Path:
    """Root of the single_agent package."""
    return Path(__file__).resolve().parent


def sa_project_root() -> Path:
    """Package root — single_agent/ itself (contains tasks/, results/, traces/)."""
    return sa_package_root()


def sa_tools_dir() -> Path:
    return sa_package_root() / "tools"


def sa_fixtures_dir() -> Path:
    return sa_package_root() / "fixtures"


def sa_fixture_domain(domain: str) -> str:
    return _FIXTURE_DOMAIN_ALIASES.get(domain, domain)


def resolve_sa_task_path(
    spec: str | Path,
    *,
    project_root: Path | None = None,
    cwd: Path | None = None,
) -> Path:
    """Resolve SA task path — searches single_agent/tasks/."""
    cwd = cwd or Path.cwd()
    project_root = project_root or sa_project_root()
    raw = Path(spec)

    candidates: list[Path] = []

    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(cwd / raw)
        candidates.append(project_root / raw)

    # Search tasks/ subdirectories inside single_agent/
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
    """Load tool catalog from single_agent/tools/ with SA fixtures."""
    tools_dir = sa_tools_dir()
    path = tools_dir / f"{domain}.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)
    catalog = ToolCatalog(**data)

    # Load fixture data from SA fixtures directory
    fixtures_dir = sa_fixtures_dir() / sa_fixture_domain(domain)
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
    synthesize_useful_tool_rules(task, catalog)
    synthesize_communication_rules(task)  # no-op for single agent
    return task, catalog
