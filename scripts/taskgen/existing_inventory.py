"""Load existing HarnessAudit-Bench inventory for a domain.

Provides the complete context needed by generation stages:
- Existing tasks (goals, agents, tool sets, categories)
- Tool catalog (names, is_resource flags, params)
- Fixtures (entity IDs, seed data schemas)
- Bank schema (table/column names for sql_assert validation)
- Recognizer registry (data_class → patterns)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.taskgen.domain_config import DomainConfig, MASP_ROOT
from scripts.taskgen.yaml_io import read_yaml, read_json


@dataclass
class ToolInfo:
    """Summary of a tool from the domain catalog."""
    name: str
    description: str
    is_resource: bool
    params: dict[str, str]
    fixture: str | None = None
    fixture_key: str | None = None


@dataclass
class TaskSummary:
    """Summary of an existing task for dedup and context."""
    task_id: str
    domain: str
    category: str
    goal: str
    modality: str
    agent_roles: list[str]
    useful_tools_by_role: dict[str, list[str]]
    data_classes_used: list[str]


@dataclass
class DomainInventory:
    """Complete inventory for a single domain."""

    config: DomainConfig

    # Tool catalog
    tools: list[ToolInfo] = field(default_factory=list)
    tool_names: set[str] = field(default_factory=set)
    resource_tool_names: set[str] = field(default_factory=set)
    tools_yaml_text: str = ""

    # Existing tasks
    tasks: list[TaskSummary] = field(default_factory=list)
    existing_task_ids: set[str] = field(default_factory=set)
    existing_goals: list[str] = field(default_factory=list)
    existing_categories: set[str] = field(default_factory=set)

    # Fixtures
    entity_ids: set[str] = field(default_factory=set)
    fixture_files: dict[str, Any] = field(default_factory=dict)

    # Bank schema
    bank_tables: list[str] = field(default_factory=list)
    bank_schema_sql: str = ""
    # table_name → [column_name, ...] mapping from PRAGMA introspection
    bank_columns: dict[str, list[str]] = field(default_factory=dict)

    # Recognizer
    data_classes: list[str] = field(default_factory=list)

    # Exemplar task YAMLs (full text for prompt context)
    exemplar_yamls: dict[str, str] = field(default_factory=dict)


def _load_tools(config: DomainConfig) -> tuple[list[ToolInfo], str]:
    """Load tool catalog YAML, return tool list and raw YAML text."""
    raw = config.tools_yaml.read_text()
    data = read_yaml(config.tools_yaml)
    tools_data = data if isinstance(data, list) else data.get("tools", [])
    tools = []
    for t in tools_data:
        tools.append(ToolInfo(
            name=t["name"],
            description=t.get("description", ""),
            is_resource=t.get("is_resource", False),
            params={k: str(v) for k, v in (t.get("params") or {}).items()},
            fixture=t.get("fixture"),
            fixture_key=t.get("fixture_key"),
        ))
    return tools, raw


def _load_existing_tasks(config: DomainConfig) -> list[TaskSummary]:
    """Load all existing task YAMLs for a domain."""
    tasks = []
    for yaml_path in sorted(config.tasks_dir.rglob("*.yaml")):
        try:
            data = read_yaml(yaml_path)
        except Exception:
            continue
        if not isinstance(data, dict) or "task_id" not in data:
            continue

        agents = data.get("agents", [])
        useful_by_role: dict[str, list[str]] = {}
        for agent in agents:
            role = agent.get("role", "")
            tn = agent.get("tool_necessity", {})
            useful_by_role[role] = tn.get("useful", [])

        data_classes = []
        for rule in data.get("access_rules", []):
            if rule.get("access_type") == "information.data_leak":
                dc = (rule.get("match") or {}).get("data_class", "")
                if dc and dc not in data_classes:
                    data_classes.append(dc)

        tasks.append(TaskSummary(
            task_id=data["task_id"],
            domain=data.get("domain", config.domain),
            category=data.get("category", ""),
            goal=data.get("goal", ""),
            modality=data.get("modality", "text"),
            agent_roles=[a.get("role", "") for a in agents],
            useful_tools_by_role=useful_by_role,
            data_classes_used=data_classes,
        ))
    return tasks


def _load_fixtures(config: DomainConfig) -> tuple[dict[str, Any], set[str]]:
    """Load all fixture JSON files and extract entity IDs."""
    fixture_files: dict[str, Any] = {}
    entity_ids: set[str] = set()

    if not config.fixtures_dir.exists():
        return fixture_files, entity_ids

    for json_path in sorted(config.fixtures_dir.glob("*.json")):
        data = read_json(json_path)
        fixture_files[json_path.name] = data
        _extract_ids(data, entity_ids)

    return fixture_files, entity_ids


def _extract_ids(data: Any, ids: set[str], depth: int = 0) -> None:
    """Recursively extract string values that look like entity IDs."""
    if depth > 8:
        return
    if isinstance(data, dict):
        for k, v in data.items():
            # Keys themselves may be entity IDs (possibly with # prefix)
            stripped_k = k.lstrip("#")
            if _looks_like_id(stripped_k):
                ids.add(stripped_k)
            if isinstance(v, str) and (
                k.endswith("_id") or k == "id" or k.endswith("_key")
                or k.endswith("_no") or k.endswith("_code")
            ):
                ids.add(v)
            if isinstance(v, str) and _looks_like_id(v):
                ids.add(v)
            _extract_ids(v, ids, depth + 1)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str) and _looks_like_id(item):
                ids.add(item)
            _extract_ids(item, ids, depth + 1)


_ID_RE = re.compile(
    r"^[A-Z]{1,10}[-_][A-Za-z0-9_-]{1,30}$"  # PREFIX-SUFFIX (e.g., EC-20451)
    r"|^[A-Z]{2,6}\d{4,}$"                     # PREFIXNUMBER (e.g., INC0017245)
)


def _looks_like_id(value: str) -> bool:
    """Heuristic: strings like EC-20451, PF-4821, INC0017245, CUS-4401, KB-214, etc."""
    return bool(_ID_RE.match(value))


def _load_bank_schema(
    config: DomainConfig,
) -> tuple[list[str], str, dict[str, list[str]]]:
    """Extract table names, raw schema SQL, and per-table columns from bank module.

    Uses PRAGMA table_info on a temporary in-memory DB seeded with the bank's
    SCHEMA_SQL constant for accurate column introspection.
    """
    import importlib
    import sqlite3

    bank_path = MASP_ROOT / "banks" / f"{config.domain}.py"
    if not bank_path.exists():
        return [], "", {}

    # Import the bank module to get SCHEMA_SQL
    module_name = config.bank_module
    try:
        mod = importlib.import_module(module_name)
        schema_sql: str = getattr(mod, "SCHEMA_SQL", "")
    except Exception:
        # Fallback: regex extraction from source
        content = bank_path.read_text()
        tables = re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)", content)
        return tables, "", {}

    if not schema_sql:
        return [], "", {}

    # Create a temporary in-memory DB with just the schema (no seeding)
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(schema_sql)
    except Exception:
        conn.close()
        tables = re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)", schema_sql)
        return tables, schema_sql, {}

    # Introspect tables and columns via PRAGMA
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in cursor.fetchall()]

    columns: dict[str, list[str]] = {}
    for table in tables:
        cur = conn.execute(f"PRAGMA table_info({table})")
        columns[table] = [row[1] for row in cur.fetchall()]

    conn.close()
    return tables, schema_sql, columns


def _load_recognizer_classes(domain: str) -> list[str]:
    """Get registered data_class names for a domain from the recognizer."""
    from masp import recognizer
    domain_patterns = recognizer._REGISTRY.get(domain, {})
    return list(domain_patterns.keys())


def _load_exemplar_yamls(config: DomainConfig) -> dict[str, str]:
    """Load exemplar task YAMLs as raw text for prompt context."""
    exemplars: dict[str, str] = {}
    for task_id in config.exemplar_task_ids:
        for yaml_path in config.tasks_dir.rglob(f"{task_id}.yaml"):
            exemplars[task_id] = yaml_path.read_text()
            break
    return exemplars


def load_domain_inventory(config: DomainConfig) -> DomainInventory:
    """Build complete inventory for a domain."""
    tools, tools_yaml_text = _load_tools(config)
    tasks = _load_existing_tasks(config)
    fixture_files, entity_ids = _load_fixtures(config)
    bank_tables, bank_schema_sql, bank_columns = _load_bank_schema(config)
    data_classes = _load_recognizer_classes(config.recognizer_domain)
    exemplar_yamls = _load_exemplar_yamls(config)

    return DomainInventory(
        config=config,
        tools=tools,
        tool_names={t.name for t in tools},
        resource_tool_names={t.name for t in tools if t.is_resource},
        tools_yaml_text=tools_yaml_text,
        tasks=tasks,
        existing_task_ids={t.task_id for t in tasks},
        existing_goals=[t.goal for t in tasks],
        existing_categories={t.category for t in tasks},
        fixture_files=fixture_files,
        entity_ids=entity_ids,
        bank_tables=bank_tables,
        bank_schema_sql=bank_schema_sql,
        bank_columns=bank_columns,
        data_classes=data_classes,
        exemplar_yamls=exemplar_yamls,
    )
