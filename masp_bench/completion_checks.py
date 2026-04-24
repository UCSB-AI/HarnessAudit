"""Deterministic completion-check rules for Layer 3 evaluation."""

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


def _check_tool_called(check: dict[str, Any], ctx: CompletionCheckContext) -> bool:
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
    if workspace is None:
        return None
    file_path = Path(workspace.path) / relative_path
    if not file_path.exists():
        return None
    return file_path.read_text(errors="replace")


def _check_file_contains(check: dict[str, Any], ctx: CompletionCheckContext) -> bool:
    contents = _read_workspace_file(ctx.workspace, check.get("file", ""))
    pattern = check.get("pattern", "")
    return contents is not None and pattern in contents


def _check_file_not_contains(check: dict[str, Any], ctx: CompletionCheckContext) -> bool:
    contents = _read_workspace_file(ctx.workspace, check.get("file", ""))
    pattern = check.get("pattern", "")
    return contents is None or pattern not in contents


COMPLETION_RULE_HANDLERS: dict[str, CompletionRuleHandler] = {
    "tool_called": _check_tool_called,
    "final_output_contains": _check_final_output_contains,
    "sql_assert": _check_sql_assert,
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
