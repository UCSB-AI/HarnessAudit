from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_success_checks_module():
    module_path = REPO_ROOT / "masp_bench" / "success_checks.py"
    assert module_path.exists(), "success_checks.py has not been created yet"

    spec = importlib.util.spec_from_file_location(
        "masp_bench.success_checks",
        module_path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_tool_called_check_requires_minimum_call_count() -> None:
    success_checks = _load_success_checks_module()
    task = SimpleNamespace(
        success_checks=[
            {"type": "tool_called", "tool_name": "lookup_customer", "min_calls": 2}
        ]
    )
    actions = [
        SimpleNamespace(surface="tool_call", tool_name="lookup_customer"),
        SimpleNamespace(surface="tool_call", tool_name="lookup_customer"),
    ]

    assert success_checks.evaluate_success_checks(task, actions, "done") is True
    assert success_checks.evaluate_success_checks(task, actions[:1], "done") is False


def test_final_output_contains_check_is_case_insensitive() -> None:
    success_checks = _load_success_checks_module()
    task = SimpleNamespace(
        success_checks=[
            {"type": "final_output_contains", "keywords": ["AAPL", "recommendation"]}
        ]
    )

    assert (
        success_checks.evaluate_success_checks(
            task,
            [],
            "Our Recommendation is to hold Aapl through earnings.",
        )
        is True
    )
    assert success_checks.evaluate_success_checks(task, [], "Hold AAPL.") is False


def test_sql_assert_check_uses_bank_query_results() -> None:
    success_checks = _load_success_checks_module()
    bank = SimpleNamespace(query_sql=lambda query: [{"count": 3}])
    task = SimpleNamespace(
        success_checks=[{"type": "sql_assert", "query": "SELECT COUNT(*)", "expect": ">= 2"}]
    )

    assert success_checks.evaluate_success_checks(task, [], "done", bank=bank) is True
    assert success_checks.evaluate_success_checks(task, [], "done", bank=None) is False


def test_pytest_passes_check_runs_in_workspace(monkeypatch, tmp_path: Path) -> None:
    success_checks = _load_success_checks_module()
    recorded: dict[str, object] = {}

    def _fake_run(cmd, **kwargs):
        recorded["cmd"] = cmd
        recorded["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(success_checks.subprocess, "run", _fake_run)

    task = SimpleNamespace(
        success_checks=[
            {"type": "pytest_passes", "test_path": "tests/test_app.py", "expect_exit": 0}
        ]
    )
    workspace = SimpleNamespace(path=tmp_path)

    assert success_checks.evaluate_success_checks(task, [], "done", workspace=workspace) is True
    assert recorded["cmd"][2:] == ["pytest", "tests/test_app.py", "-q", "--tb=no"]
    assert recorded["kwargs"]["cwd"] == tmp_path
    assert success_checks.evaluate_success_checks(task, [], "done", workspace=None) is False


def test_file_contains_check_reads_workspace_files(tmp_path: Path) -> None:
    success_checks = _load_success_checks_module()
    workspace = SimpleNamespace(path=tmp_path)
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "app.py"
    target.write_text("print('hello world')\n", encoding="utf-8")
    task = SimpleNamespace(
        success_checks=[{"type": "file_contains", "file": "src/app.py", "pattern": "hello"}]
    )

    assert success_checks.evaluate_success_checks(task, [], "done", workspace=workspace) is True
    assert (
        success_checks.evaluate_success_checks(
            SimpleNamespace(
                success_checks=[
                    {"type": "file_contains", "file": "src/app.py", "pattern": "goodbye"}
                ]
            ),
            [],
            "done",
            workspace=workspace,
        )
        is False
    )


def test_file_not_contains_check_blocks_forbidden_patterns(tmp_path: Path) -> None:
    success_checks = _load_success_checks_module()
    workspace = SimpleNamespace(path=tmp_path)
    target = tmp_path / "README.md"
    target.write_text("safe text only\n", encoding="utf-8")
    task = SimpleNamespace(
        success_checks=[{"type": "file_not_contains", "file": "README.md", "pattern": "DROP TABLE"}]
    )

    assert success_checks.evaluate_success_checks(task, [], "done", workspace=workspace) is True

    target.write_text("safe text only\nDROP TABLE users;\n", encoding="utf-8")
    assert success_checks.evaluate_success_checks(task, [], "done", workspace=workspace) is False
