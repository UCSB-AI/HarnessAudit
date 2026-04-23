"""Task runner — framework-agnostic orchestration with adapters."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from masp_bench.banks import get_bank
from masp_bench.frameworks import (
    RunContext,
    get_framework_adapter,
)
from masp_bench.frameworks.action_sink import ActionSink
from masp_bench.frameworks.common import build_task_input
from masp_bench.schemas.actions import ObservableAction
from masp_bench.schemas.task import TaskConfig, ToolCatalog
from masp_bench.schemas.trace import ExecutionTrace
from masp_bench.success_checks import evaluate_success_checks
from masp_bench.trace.events import AgentInfo, TraceStart
from masp_bench.trace.writer import TraceWriter
from masp_bench.workspace import Workspace, WorkspaceManager


def framework_run_id_slug(framework: str) -> str:
    """Map framework id to stable run-id prefix."""
    mapping = {"clawteam": "ct", "oai": "oa"}
    return mapping.get(framework, framework[:3])


def next_run_id(
    model: str = "claude-sonnet-4",
    framework_slug: str = "ct",
    harness_slug: str = "claude",
) -> str:
    """Return a unique run id: ``{framework_slug}-{harness}-{model}-{uuid}``."""
    short = uuid.uuid4().hex[:8]
    return f"{framework_slug}-{harness_slug}-{model}-{short}"


def _build_execution_trace(
    *,
    run_id: str,
    task_id: str,
    domain: str,
    framework: str,
    llm: str,
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
        defense_mode="monitor",
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
        model: str = "claude-sonnet-4",
        framework: str = "clawteam",
        harness: str = "claude",
        trace_writer: TraceWriter | None = None,
        max_turns: int = 30,
        workspace_manager: WorkspaceManager | None = None,
        bank_factory: Any = None,
        agent_timeout: int = 300,
    ) -> None:
        self.task = task
        self.catalog = catalog
        self.model = model
        self.framework = framework
        self.harness = harness
        self._trace_writer = trace_writer
        self.max_turns = max_turns
        self._workspace_manager = workspace_manager
        self._bank_factory = bank_factory or get_bank
        self._agent_timeout = agent_timeout
        self._bank: Any = None
        self._workspace: Workspace | None = None

    def run(
        self,
        run_id: str | None = None,
    ) -> ExecutionTrace:
        """Synchronous entry point."""
        return asyncio.run(self._run_async(run_id=run_id))

    async def _run_async(
        self,
        run_id: str | None = None,
    ) -> ExecutionTrace:
        run_id = run_id or next_run_id(
            model=self.model,
            framework_slug=framework_run_id_slug(self.framework),
            harness_slug=self.harness,
        )
        trace_writer = self._trace_writer
        run_started_at = datetime.now(timezone.utc)

        action_sink = ActionSink(
            run_id=run_id,
            trace_writer=trace_writer,
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
                        defense_mode="monitor",
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

            adapter = get_framework_adapter(
                self.framework,
                agent_timeout=self._agent_timeout,
                harness=self.harness,
            )
            outcome = await adapter.run(
                RunContext(
                    run_id=run_id,
                    task=self.task,
                    catalog=self.catalog,
                    model=self.model,
                    max_turns=self.max_turns,
                    bank=self._bank,
                    workspace=self._workspace,
                    action_sink=action_sink,
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
        return evaluate_success_checks(
            self.task,
            actions,
            final_output,
            bank=self._bank,
            workspace=self._workspace,
        )


__all__ = [
    "BenchmarkRunner",
    "build_task_input",
    "framework_run_id_slug",
    "next_run_id",
]
