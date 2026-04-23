"""Success-check registry for task completion evaluation."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class SuccessCheckContext:
    actions: list[Any]
    final_output: str
    bank: Any = None
    workspace: Any = None


SuccessCheckHandler = Callable[[dict[str, Any], SuccessCheckContext], bool]


def _check_tool_called(check: dict[str, Any], ctx: SuccessCheckContext) -> bool:
    tool_name = check.get("tool_name", "")
    min_calls = check.get("min_calls", 1)
    actual = sum(
        1
        for action in ctx.actions
        if action.surface == "tool_call" and action.tool_name == tool_name
    )
    return actual >= min_calls


def _check_final_output_contains(
    check: dict[str, Any],
    ctx: SuccessCheckContext,
) -> bool:
    keywords = check.get("keywords", [])
    output_lower = ctx.final_output.lower()
    return all(str(keyword).lower() in output_lower for keyword in keywords)


def _check_sql_assert(check: dict[str, Any], ctx: SuccessCheckContext) -> bool:
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


def _check_pytest_passes(check: dict[str, Any], ctx: SuccessCheckContext) -> bool:
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
        )
    except Exception:
        return False
    return result.returncode == expect_exit


def _read_workspace_file(workspace: Any, relative_path: str) -> str | None:
    if workspace is None:
        return None
    file_path = Path(workspace.path) / relative_path
    if not file_path.exists():
        return None
    return file_path.read_text(errors="replace")


def _check_file_contains(check: dict[str, Any], ctx: SuccessCheckContext) -> bool:
    contents = _read_workspace_file(ctx.workspace, check.get("file", ""))
    pattern = check.get("pattern", "")
    return contents is not None and pattern in contents


def _check_file_not_contains(check: dict[str, Any], ctx: SuccessCheckContext) -> bool:
    contents = _read_workspace_file(ctx.workspace, check.get("file", ""))
    pattern = check.get("pattern", "")
    return contents is None or pattern not in contents


SUCCESS_CHECK_HANDLERS: dict[str, SuccessCheckHandler] = {
    "tool_called": _check_tool_called,
    "final_output_contains": _check_final_output_contains,
    "sql_assert": _check_sql_assert,
    "pytest_passes": _check_pytest_passes,
    "file_contains": _check_file_contains,
    "file_not_contains": _check_file_not_contains,
}


def evaluate_success_checks(
    task: Any,
    actions: list[Any],
    final_output: str,
    *,
    bank: Any = None,
    workspace: Any = None,
) -> bool:
    """Evaluate the task's success_checks against the run artifacts."""
    checks = getattr(task, "success_checks", None)
    if not checks:
        return True

    ctx = SuccessCheckContext(
        actions=actions,
        final_output=final_output,
        bank=bank,
        workspace=workspace,
    )
    for check in checks:
        handler = SUCCESS_CHECK_HANDLERS.get(check.get("type"))
        if handler is None:
            continue
        if not handler(check, ctx):
            return False
    return True


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
            except (ValueError, TypeError):
                return fn(str(actual), str(rhs))
    try:
        return float(actual) == float(expect)
    except (ValueError, TypeError):
        return str(actual) == expect


__all__ = [
    "SUCCESS_CHECK_HANDLERS",
    "SuccessCheckContext",
    "evaluate_success_checks",
]
