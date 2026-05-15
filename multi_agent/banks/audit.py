"""Shared audit-log reconciliation helpers for domain banks."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
import sqlite3
from typing import Any, Callable

log = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EMPTY_ROLE_VALUES = {"", "unknown"}


@dataclass(frozen=True)
class ToolWriteBackfill:
    """Map a tool call to a write table role column that should be backfilled."""

    table: str
    role_column: str
    tool_name: str
    order_column: str = "seq"


def _quote_ident(identifier: str) -> str:
    if not _IDENT_RE.match(identifier):
        raise ValueError(f"Unsafe SQLite identifier: {identifier!r}")
    return f'"{identifier}"'


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    table_sql = _quote_ident(table)
    return {
        str(row["name"] if isinstance(row, sqlite3.Row) else row[1])
        for row in conn.execute(f"PRAGMA table_info({table_sql})")
    }


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, sqlite3.Row):
        return row[key]
    return row[index]


def tool_actions(actions: list[Any]) -> list[Any]:
    """Return normalized tool-call actions with a tool name."""

    return [
        action for action in actions
        if str(getattr(action, "surface", "") or "") == "tool_call"
        and str(getattr(action, "tool_name", "") or "")
    ]


def action_args(action: Any) -> dict[str, Any]:
    args = getattr(action, "tool_args", None) or {}
    return args if isinstance(args, dict) else {}


def action_role(action: Any) -> str:
    return str(getattr(action, "agent_role", "") or "").strip()


def json_args(action: Any) -> str:
    return json.dumps(action_args(action), default=str)


def reconcile_audit_log(
    conn: sqlite3.Connection,
    actions: list[Any],
    derive_fields: Callable[[Any], dict[str, Any]],
    *,
    table: str = "audit_log",
    pk_column: str = "seq",
) -> None:
    """Update existing audit rows from normalized trace actions without inserting rows."""

    table_sql = _quote_ident(table)
    pk_sql = _quote_ident(pk_column)
    columns = table_columns(conn, table)
    if pk_column not in columns:
        raise ValueError(f"{table}.{pk_column} does not exist")

    rows = conn.execute(f"SELECT {pk_sql} FROM {table_sql} ORDER BY {pk_sql}").fetchall()
    actions_to_apply = tool_actions(actions)
    if len(rows) != len(actions_to_apply):
        log.warning(
            "Audit/action count mismatch for %s: rows=%d actions=%d; reconciling paired prefix",
            table,
            len(rows),
            len(actions_to_apply),
        )

    for row, action in zip(rows, actions_to_apply):
        fields = {
            key: value
            for key, value in (derive_fields(action) or {}).items()
            if key in columns and key != pk_column
        }
        if not fields:
            continue
        set_clause = ", ".join(f"{_quote_ident(key)} = ?" for key in fields)
        values = list(fields.values())
        values.append(_row_value(row, pk_column, 0))
        conn.execute(
            f"UPDATE {table_sql} SET {set_clause} WHERE {pk_sql} = ?",
            values,
        )


def backfill_write_roles(
    conn: sqlite3.Connection,
    actions: list[Any],
    specs: list[ToolWriteBackfill],
) -> None:
    """Backfill empty/unknown write-table role fields from normalized trace actions."""

    all_tool_actions = tool_actions(actions)
    for spec in specs:
        columns = table_columns(conn, spec.table)
        if spec.role_column not in columns or spec.order_column not in columns:
            raise ValueError(
                f"{spec.table} must contain {spec.role_column} and {spec.order_column}"
            )

        table_sql = _quote_ident(spec.table)
        role_sql = _quote_ident(spec.role_column)
        order_sql = _quote_ident(spec.order_column)
        matching_actions = [
            action for action in all_tool_actions
            if str(getattr(action, "tool_name", "") or "") == spec.tool_name
        ]
        if not matching_actions:
            continue

        rows = conn.execute(
            f"SELECT rowid AS _rowid, {role_sql} AS _role_value "
            f"FROM {table_sql} "
            f"WHERE {role_sql} IS NULL "
            f"   OR TRIM({role_sql}) = '' "
            f"   OR LOWER(TRIM({role_sql})) = 'unknown' "
            f"ORDER BY {order_sql}, rowid"
        ).fetchall()
        if len(rows) != len(matching_actions):
            log.warning(
                "Write-role/action count mismatch for %s.%s via %s: rows=%d actions=%d; "
                "reconciling paired prefix",
                spec.table,
                spec.role_column,
                spec.tool_name,
                len(rows),
                len(matching_actions),
            )

        for row, action in zip(rows, matching_actions):
            role = action_role(action)
            if not role or role.lower() in _EMPTY_ROLE_VALUES:
                continue
            conn.execute(
                f"UPDATE {table_sql} SET {role_sql} = ? WHERE rowid = ?",
                (role, _row_value(row, "_rowid", 0)),
            )
