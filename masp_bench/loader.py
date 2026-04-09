"""Load task configs and tool catalogs from YAML files."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from masp_bench.schemas.task import TaskConfig, ToolCatalog


def load_task(path: str | Path) -> TaskConfig:
    """Load a TaskConfig from a YAML file."""
    path = Path(path)
    with open(path) as f:
        data = yaml.safe_load(f)
    return TaskConfig(**data)


def load_tool_catalog(domain: str, tools_dir: str | Path | None = None) -> ToolCatalog:
    """Load a ToolCatalog from tools/<domain>.yaml.

    If tools_dir is None, looks in masp_bench/tools/ relative to this package.
    Also loads fixture JSON files from fixtures/<domain>/ for tools that reference them.
    """
    if tools_dir is None:
        tools_dir = Path(__file__).parent / "tools"
    else:
        tools_dir = Path(tools_dir)

    path = tools_dir / f"{domain}.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)
    catalog = ToolCatalog(**data)

    # Load fixture data for tools that reference fixture files
    fixtures_dir = Path(__file__).parent / "fixtures" / domain
    for tool in catalog.tools:
        if tool.fixture and tool.fixture_key:
            fixture_path = fixtures_dir / tool.fixture
            if fixture_path.exists():
                with open(fixture_path) as f:
                    tool.fixture_data = json.load(f)

    return catalog


def load_task_with_tools(
    task_path: str | Path,
    tools_dir: str | Path | None = None,
) -> tuple[TaskConfig, ToolCatalog]:
    """Load a task and its domain's tool catalog together."""
    task = load_task(task_path)
    catalog = load_tool_catalog(task.domain, tools_dir)
    return task, catalog
