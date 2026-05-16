"""Deterministic completion-check rules for L2 TCR evaluation."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(slots=True)
class CompletionCheckContext:
    actions: list[Any]
    final_output: str
    bank: Any = None
    workspace: Any = None


CompletionRuleHandler = Callable[[dict[str, Any], CompletionCheckContext], bool]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return list(value)
    return [value]


def _surface(action: Any) -> str:
    surface = getattr(action, "surface", "") or ""
    return str(getattr(surface, "value", surface))


def _tool_args(action: Any) -> dict[str, Any]:
    args = getattr(action, "tool_args", {}) or {}
    return args if isinstance(args, dict) else {}


def _scalar_values_match(actual: Any, expected: Any) -> bool:
    if actual == expected:
        return True
    if actual is None or expected is None:
        return False
    try:
        return float(actual) == float(expected)
    except (TypeError, ValueError):
        return str(actual).strip() == str(expected).strip()


def _mapping_matches(
    actual: dict[str, Any],
    expected: dict[str, Any],
    *,
    exact: bool = False,
) -> bool:
    if exact and set(actual) != set(expected):
        return False
    for key, expected_value in expected.items():
        if key not in actual:
            return False
        if not _value_matches(actual[key], expected_value, exact=exact):
            return False
    return True


def _value_matches(actual: Any, expected: Any, *, exact: bool = False) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and _mapping_matches(
            actual,
            expected,
            exact=exact,
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(
            _value_matches(actual_item, expected_item, exact=exact)
            for actual_item, expected_item in zip(actual, expected, strict=True)
        )
    return _scalar_values_match(actual, expected)


def _check_tool_called(check: dict[str, Any], ctx: CompletionCheckContext) -> bool:
    tool_name = check.get("tool_name", "")
    min_calls = int(check.get("min_calls", 1) or 1)
    acting_roles = {str(role) for role in _as_list(check.get("acting_role"))}
    arg_match = check.get("arg_match")
    arg_match_mode = str(check.get("arg_match_mode", "subset") or "subset").lower()
    exact_args = arg_match_mode == "exact"

    actual = 0
    for action in ctx.actions:
        if _surface(action) != "tool_call" or getattr(action, "tool_name", None) != tool_name:
            continue
        if acting_roles and str(getattr(action, "agent_role", "")) not in acting_roles:
            continue
        if arg_match is not None:
            if not isinstance(arg_match, dict):
                return False
            if not _mapping_matches(_tool_args(action), arg_match, exact=exact_args):
                continue
        actual += 1
    return actual >= min_calls


def _check_final_output_contains(
    check: dict[str, Any],
    ctx: CompletionCheckContext,
) -> bool:
    keywords = check.get("keywords", [])
    output_lower = ctx.final_output.lower()
    return all(str(keyword).lower() in output_lower for keyword in keywords)


def _check_sql_assert(check: dict[str, Any], ctx: CompletionCheckContext) -> bool:
    if ctx.bank is None:
        return False
    query = check.get("query", "")
    expect = str(check.get("expect", "")).strip()
    try:
        rows = ctx.bank.query_sql(query)
    except Exception:
        return False
    if not rows:
        actual_val: Any = None
    else:
        first = rows[0]
        actual_val = next(iter(first.values())) if first else None
    return _eval_expect(actual_val, expect)


def _check_sql_row_assert(check: dict[str, Any], ctx: CompletionCheckContext) -> bool:
    if ctx.bank is None:
        return False
    query = check.get("query", "")
    try:
        rows = ctx.bank.query_sql(query)
    except Exception:
        return False
    if not isinstance(rows, list):
        return False

    expected_rows_raw = check.get("expect_rows", [])
    if isinstance(expected_rows_raw, dict):
        expected_rows = [expected_rows_raw]
    elif isinstance(expected_rows_raw, list):
        expected_rows = expected_rows_raw
    else:
        return False

    min_matching_raw = check.get("min_matching")
    min_matching = (
        len(expected_rows)
        if min_matching_raw is None
        else int(min_matching_raw or 0)
    )
    if not expected_rows:
        if min_matching_raw is None:
            return False
        return len(rows) >= min_matching
    if min_matching <= 0:
        return True

    match_mode = str(check.get("match_mode", "contains") or "contains").lower()
    exact = match_mode == "exact"
    normalized_rows = [row for row in rows if isinstance(row, dict)]
    used_row_indexes: set[int] = set()
    matched = 0

    for expected in expected_rows:
        if not isinstance(expected, dict):
            return False
        for index, row in enumerate(normalized_rows):
            if index in used_row_indexes:
                continue
            if _mapping_matches(row, expected, exact=exact):
                used_row_indexes.add(index)
                matched += 1
                break
        if matched >= min_matching:
            return True
    return False


def _check_pytest_passes(check: dict[str, Any], ctx: CompletionCheckContext) -> bool:
    if ctx.workspace is None:
        return False
    test_path = check.get("test_path", "tests/")
    expect_exit = check.get("expect_exit", 0)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", test_path, "-q", "--tb=no"],
            cwd=ctx.workspace.path,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == expect_exit


def _read_workspace_file(workspace: Any, relative_path: str) -> str | None:
    if workspace is None or not relative_path:
        return None
    file_path = Path(workspace.path) / relative_path
    if not file_path.is_file():
        return None
    return file_path.read_text(errors="replace")


def _check_file_contains(check: dict[str, Any], ctx: CompletionCheckContext) -> bool:
    contents = _read_workspace_file(ctx.workspace, check.get("file", ""))
    pattern = check.get("pattern", "")
    return contents is not None and pattern in contents


def _check_file_not_contains(check: dict[str, Any], ctx: CompletionCheckContext) -> bool:
    contents = _read_workspace_file(ctx.workspace, check.get("file", ""))
    pattern = check.get("pattern", "")
    return contents is not None and pattern not in contents


COMPLETION_RULE_HANDLERS: dict[str, CompletionRuleHandler] = {
    "tool_called": _check_tool_called,
    "final_output_contains": _check_final_output_contains,
    "sql_assert": _check_sql_assert,
    "sql_row_assert": _check_sql_row_assert,
    "pytest_passes": _check_pytest_passes,
    "file_contains": _check_file_contains,
    "file_not_contains": _check_file_not_contains,
}

COMPLETION_RULE_TYPES = frozenset(COMPLETION_RULE_HANDLERS)


def evaluate_rule_checkpoint(
    checkpoint: dict[str, Any],
    ctx: CompletionCheckContext,
) -> bool:
    """Evaluate a deterministic completion checkpoint."""
    rule_type = checkpoint.get("rule_type")
    if not isinstance(rule_type, str):
        raise ValueError(f"Unknown completion rule_type: {rule_type!r}")
    handler = COMPLETION_RULE_HANDLERS.get(rule_type)
    if handler is None:
        raise ValueError(f"Unknown completion rule_type: {rule_type!r}")
    return handler(checkpoint, ctx)


def _eval_expect(actual: Any, expect: str) -> bool:
    """Evaluate scalar expectation expressions."""
    expect = expect.strip()
    ops = [
        (">=", lambda a, b: a >= b),
        ("<=", lambda a, b: a <= b),
        ("==", lambda a, b: a == b),
        ("!=", lambda a, b: a != b),
        (">", lambda a, b: a > b),
        ("<", lambda a, b: a < b),
    ]
    for op, fn in ops:
        if expect.startswith(op):
            rhs = expect[len(op):].strip().strip("'\"")
            try:
                return fn(float(actual), float(rhs))
            except (TypeError, ValueError):
                return fn(str(actual), str(rhs))
    try:
        return float(actual) == float(expect)
    except (TypeError, ValueError):
        return str(actual) == expect


__all__ = [
    "COMPLETION_RULE_HANDLERS",
    "COMPLETION_RULE_TYPES",
    "CompletionCheckContext",
    "evaluate_rule_checkpoint",
]
