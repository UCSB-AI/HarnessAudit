"""Task runner — framework-agnostic orchestration with AAP adapters."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from masp_bench.banks import get_bank
from masp_bench.frameworks import (
    DefaultDefenseHook,
    DefenseHook,
    DefenseMode,
    RunContext,
    get_framework_adapter,
)
from masp_bench.frameworks.action_sink import ActionSink
from masp_bench.frameworks.common import build_task_input
from masp_bench.frameworks.tool_runtime import ToolRuntime
from masp_bench.schemas.actions import ObservableAction
from masp_bench.schemas.task import TaskConfig, ToolCatalog
from masp_bench.schemas.trace import ExecutionTrace
from masp_bench.trace.events import AgentInfo, TraceStart
from masp_bench.trace.writer import TraceWriter
from masp_bench.workspace import Workspace, WorkspaceManager


def framework_run_id_slug(framework: str) -> str:
    """Map framework id to stable run-id prefix."""
    mapping = {"clawteam": "ct", "openai_agents": "oai", "autogen": "ag"}
    return mapping.get(framework, re.sub(r"[^a-z0-9]+", "", framework.lower()) or "run")


def next_run_id(
    search_dirs: Iterable[Path] | None = None,
    model: str = "gpt-5.4",
    framework_slug: str = "oai",
) -> str:
    """Return next sequential run id ``{framework_slug}-{model}-{n}``."""
    if search_dirs is None:
        project_root = Path(__file__).resolve().parent.parent
        search_dirs = [project_root / "traces", project_root / "results"]

    tail_re = re.compile(
        rf"_{re.escape(framework_slug)}-.+-(\d+)\.(?:jsonl|json|sqlite)$",
        re.IGNORECASE,
    )
    max_index = 0
    for directory in search_dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        for entry in directory.iterdir():
            match = tail_re.search(entry.name)
            if not match:
                continue
            max_index = max(max_index, int(match.group(1)))
    return f"{framework_slug}-{model}-{max_index + 1}"


def _build_execution_trace(
    *,
    run_id: str,
    task_id: str,
    domain: str,
    framework: str,
    llm: str,
    defense_mode: DefenseMode,
    actions: list[ObservableAction],
    started_at: datetime,
    task_completed: bool,
    error: str | None = None,
) -> ExecutionTrace:
    trace = ExecutionTrace(
        run_id=run_id,
        task_id=task_id,
        domain=domain,
        framework=framework,
        llm=llm,
        defense_mode=defense_mode,
        actions=actions,
        decisions=[],
        task_completed=task_completed,
        started_at=started_at,
        error=error,
    )
    trace.finalize()
    return trace


class BenchmarkRunner:
    """Framework-agnostic benchmark runner with pluggable adapters."""

    def __init__(
        self,
        task: TaskConfig,
        catalog: ToolCatalog,
        model: str = "gpt-5.4",
        framework: str = "clawteam",
        trace_writer: TraceWriter | None = None,
        max_turns: int = 30,
        workspace_manager: WorkspaceManager | None = None,
        defense_hook: DefenseHook | None = None,
        bank_factory: Any = None,
    ) -> None:
        self.task = task
        self.catalog = catalog
        self.model = model
        self.framework = framework
        self._trace_writer = trace_writer
        self.max_turns = max_turns
        self._workspace_manager = workspace_manager
        self._defense_hook = defense_hook or DefaultDefenseHook()
        self._bank_factory = bank_factory or get_bank
        self._bank: Any = None
        self._workspace: Workspace | None = None

    def run(
        self,
        defense_mode: DefenseMode = "monitor",
        run_id: str | None = None,
    ) -> ExecutionTrace:
        """Synchronous entry point."""
        return asyncio.run(self._run_async(defense_mode=defense_mode, run_id=run_id))

    async def _run_async(
        self,
        defense_mode: DefenseMode,
        run_id: str | None = None,
    ) -> ExecutionTrace:
        run_id = run_id or next_run_id(
            model=self.model,
            framework_slug=framework_run_id_slug(self.framework),
        )
        trace_writer = self._trace_writer
        run_started_at = datetime.now(timezone.utc)

        action_sink = ActionSink(
            run_id=run_id,
            defense_mode=defense_mode,
            trace_writer=trace_writer,
            defense_hook=self._defense_hook,
        )
        final_text = ""
        task_completed = False
        run_error: str | None = None

        if self.task.fixture and self._workspace_manager:
            self._workspace = self._workspace_manager.create(self.task.fixture, run_id)

        try:
            self.close_bank()
            self._bank = self._bank_factory(self.task.domain)
            if self._bank is not None and hasattr(self._bank, "apply_task_metadata"):
                self._bank.apply_task_metadata(self.task.metadata)

            if trace_writer:
                trace_writer.write_event(
                    TraceStart(
                        trace_id=run_id,
                        task_id=self.task.task_id,
                        domain=self.task.domain,
                        framework=self.framework,
                        model=self.model,
                        defense_mode=defense_mode,
                        agents=[
                            AgentInfo(
                                agent_id=agent_cfg.role,
                                role=agent_cfg.role,
                                allowed_tools=agent_cfg.allowed_tools,
                            )
                            for agent_cfg in self.task.agents
                        ],
                        n_rules=len(self.task.access_rules),
                    )
                )

            tool_runtime = ToolRuntime(
                run_id=run_id,
                defense_mode=defense_mode,
                bank=self._bank,
                workspace=self._workspace,
                defense_hook=self._defense_hook,
            )
            adapter = get_framework_adapter(self.framework)
            outcome = await adapter.run(
                RunContext(
                    run_id=run_id,
                    task=self.task,
                    catalog=self.catalog,
                    model=self.model,
                    framework=self.framework,
                    defense_mode=defense_mode,
                    max_turns=self.max_turns,
                    trace_writer=trace_writer,
                    bank=self._bank,
                    workspace=self._workspace,
                    action_sink=action_sink,
                    tool_runtime=tool_runtime,
                ),
                build_task_input(self.task),
            )
            final_text = outcome.final_output
            run_error = outcome.error
            task_completed = self._check_success(action_sink.actions, final_text)
        finally:
            if self._workspace and self._workspace_manager:
                keep = not task_completed or os.getenv("MASP_KEEP_WORKSPACES") == "1"
                self._workspace_manager.destroy(self._workspace, keep=keep)
                self._workspace = None

        return _build_execution_trace(
            run_id=run_id,
            task_id=self.task.task_id,
            domain=self.task.domain,
            framework=self.framework,
            llm=self.model,
            defense_mode=defense_mode,
            actions=action_sink.actions,
            started_at=run_started_at,
            task_completed=task_completed,
            error=run_error,
        )

    def dump_bank_to_file(self, path: Any) -> bool:
        """Dump bank SQLite DB if available."""
        bank = self._bank
        if bank is None:
            return False
        try:
            bank.dump_to_file(Path(path))
            return True
        except Exception:
            return False

    def close_bank(self) -> None:
        """Close and clear the current per-run bank if present."""
        bank = self._bank
        self._bank = None
        if bank is None:
            return
        close_fn = getattr(bank, "close", None)
        if not callable(close_fn):
            return
        try:
            close_fn()
        except Exception:
            pass

    def _check_success(self, actions: list[ObservableAction], final_output: str) -> bool:
        """Evaluate success_checks from task config."""
        import subprocess as _subprocess

        if not self.task.success_checks:
            return True

        for check in self.task.success_checks:
            check_type = check.get("type")

            if check_type == "tool_called":
                tool_name = check.get("tool_name", "")
                min_calls = check.get("min_calls", 1)
                actual = sum(
                    1
                    for action in actions
                    if action.surface == "tool_call" and action.tool_name == tool_name
                )
                if actual < min_calls:
                    return False

            elif check_type == "final_output_contains":
                keywords = check.get("keywords", [])
                output_lower = final_output.lower()
                for kw in keywords:
                    if kw.lower() not in output_lower:
                        return False

            elif check_type == "sql_assert":
                if self._bank is None:
                    return False
                query = check.get("query", "")
                expect = str(check.get("expect", "")).strip()
                try:
                    rows = self._bank.query_sql(query)
                except Exception:
                    return False
                if not rows:
                    actual_val: Any = None
                else:
                    first = rows[0]
                    actual_val = next(iter(first.values())) if first else None
                if not _eval_expect(actual_val, expect):
                    return False

            elif check_type == "pytest_passes":
                if self._workspace is None:
                    return False
                test_path = check.get("test_path", "tests/")
                expect_exit = check.get("expect_exit", 0)
                try:
                    import sys as _sys

                    result = _subprocess.run(
                        [_sys.executable, "-m", "pytest", test_path, "-q", "--tb=no"],
                        cwd=self._workspace.path,
                        capture_output=True,
                        text=True,
                        timeout=180,
                    )
                    if result.returncode != expect_exit:
                        return False
                except Exception:
                    return False

            elif check_type == "file_contains":
                if self._workspace is None:
                    return False
                file_path = self._workspace.path / check.get("file", "")
                pattern = check.get("pattern", "")
                if not file_path.exists() or pattern not in file_path.read_text(errors="replace"):
                    return False

            elif check_type == "file_not_contains":
                if self._workspace is None:
                    return False
                file_path = self._workspace.path / check.get("file", "")
                pattern = check.get("pattern", "")
                if file_path.exists() and pattern in file_path.read_text(errors="replace"):
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
    "BenchmarkRunner",
    "DefenseMode",
    "build_task_input",
    "framework_run_id_slug",
    "next_run_id",
]
