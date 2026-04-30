"""V7: Validate sql_assert checkpoint queries reference valid bank tables and columns."""

from __future__ import annotations

import re
from pathlib import Path

from scripts.taskgen.existing_inventory import DomainInventory
from scripts.taskgen.yaml_io import read_yaml


def _strip_string_literals(query: str) -> str:
    """Remove single-quoted string literals from SQL to avoid false column matches."""
    return re.sub(r"'[^']*'", "''", query)


def _extract_column_refs(query: str) -> list[str]:
    """Extract likely column name references from a SQL query.

    Only looks at the LEFT side of comparison operators in WHERE/AND/OR clauses,
    which is where column names appear in standard SQL patterns.
    """
    # Strip string literals first to avoid matching values like 'diallo@example.com'
    clean = _strip_string_literals(query)

    columns: list[str] = []

    # Match: keyword column_name OP ...
    # WHERE col = ..., AND col = ..., OR col LIKE ...
    for m in re.finditer(
        r"(?:WHERE|AND|OR|ON)\s+(\w+)\s*(?:=|!=|<>|>=|<=|>|<|\s+(?:LIKE|IN|IS|BETWEEN))",
        clean,
        re.IGNORECASE,
    ):
        col = m.group(1)
        # Skip SQL keywords
        if col.upper() in {
            "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "EXISTS",
            "COUNT", "SUM", "AVG", "MIN", "MAX", "DISTINCT", "NULL",
        }:
            continue
        columns.append(col)

    return columns


def validate_sql(yaml_path: Path, inventory: DomainInventory) -> list[str]:
    """Check sql_assert queries reference tables and columns that exist in the bank schema."""
    errors: list[str] = []
    data = read_yaml(yaml_path)
    task_id = data.get("task_id", yaml_path.stem)
    valid_tables = set(inventory.bank_tables)
    table_columns = inventory.bank_columns  # {table: [col, ...]}

    if not valid_tables:
        return errors

    for cp in data.get("completion_checkpoints", []):
        if cp.get("rule_type") != "sql_assert":
            continue
        query = cp.get("query", "")
        name = cp.get("name", "?")

        # Check for deprecated expected_min key
        if "expected_min" in cp and "expect" not in cp:
            errors.append(
                f"{task_id} checkpoint '{name}' uses deprecated "
                f"'expected_min' key — must use 'expect' instead"
            )

        # Extract table names from FROM and JOIN clauses
        referenced_tables = set(
            re.findall(r"\bFROM\s+(\w+)|\bJOIN\s+(\w+)", query, re.IGNORECASE)
        )
        flat_tables = {t for pair in referenced_tables for t in pair if t}

        for table in flat_tables:
            if table not in valid_tables:
                errors.append(
                    f"{task_id} checkpoint '{name}' sql_assert references "
                    f"table '{table}' not in bank schema. "
                    f"Valid tables: {sorted(valid_tables)}"
                )

        # Column validation (only when bank_columns is available)
        if not table_columns:
            continue

        col_refs = _extract_column_refs(query)
        for col in col_refs:
            # Check if the column exists in any of the referenced tables
            found = False
            for table in flat_tables:
                if table in table_columns and col in table_columns[table]:
                    found = True
                    break
            if not found and flat_tables:
                valid_cols: set[str] = set()
                for table in flat_tables:
                    if table in table_columns:
                        valid_cols.update(table_columns[table])
                errors.append(
                    f"{task_id} checkpoint '{name}' sql_assert references "
                    f"column '{col}' not found in tables {sorted(flat_tables)}. "
                    f"Valid columns: {sorted(valid_cols)}"
                )

    return errors
