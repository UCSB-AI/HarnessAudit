"""Task runner — framework-agnostic orchestration with adapters."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from multi_agent.banks import get_bank
from multi_agent.completion_judge import (
    CompletionEvaluation,
    evaluate_completion_checkpoints,
)
from multi_agent.frameworks import (
    RunContext,
    get_framework_adapter,
)
from multi_agent.frameworks.core.action_sink import ActionSink
from multi_agent.frameworks.core.sde_policy import SDE_DOMAIN
from multi_agent.frameworks.core.task_input import build_task_input
from multi_agent.perturbation import PerturbationConfig, apply_task_perturbation
from multi_agent.schemas.actions import ObservableAction
from multi_agent.schemas.task import TaskConfig, ToolCatalog
from multi_agent.schemas.trace import ExecutionTrace
from multi_agent.trace.events import AgentInfo, TraceStart
from multi_agent.trace.writer import TraceWriter
from multi_agent.workspace import Workspace, WorkspaceManager


def framework_run_id_slug(framework: str) -> str:
    """Map framework id to stable run-id prefix."""
    mapping = {"clawteam": "ct", "oai": "oa", "adk": "adk", "openclaw_local": "ol"}
    return mapping.get(framework, framework[:3])


def next_run_id(
    model: str = "sonnet",
    framework_slug: str = "ct",
    harness_slug: str = "claude",
) -> str:
    """Return a unique run id: ``{framework_slug}-{harness}-{model}-{uuid}``."""
    short = uuid.uuid4().hex[:8]
    safe_model = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "model"
    return f"{framework_slug}-{harness_slug}-{safe_model}-{short}"


def _build_execution_trace(
    *,
    run_id: str,
    task_id: str,
    domain: str,
    framework: str,
    llm: str,
    actions: list[ObservableAction],
    started_at: datetime,
    completion: CompletionEvaluation,
    error: str | None = None,
    warnings: list[str] | None = None,
    perturbation: dict[str, Any] | None = None,
    workspace_path: str | None = None,
) -> ExecutionTrace:
    trace = ExecutionTrace(
        run_id=run_id,
        task_id=task_id,
        domain=domain,
        framework=framework,
        llm=llm,
        actions=actions,
        decisions=[],
        task_completion_score=completion.task_completion_score,
        deterministic_completion_score=completion.deterministic_completion_score,
        deterministic_completion_weight=completion.deterministic_completion_weight,
        deterministic_completion_contribution=(
            completion.deterministic_completion_contribution
        ),
        llm_completion_score=completion.llm_completion_score,
        llm_completion_weight=completion.llm_completion_weight,
        llm_completion_contribution=completion.llm_completion_contribution,
        completion_weight_total=completion.completion_weight_total,
        workspace_path=workspace_path,
        started_at=started_at,
        error=error,
        warnings=warnings or [],
        perturbation=perturbation,
        perturbation_delivered=None,
        stability_score=None,
        stable=None,
    )
    trace.finalize()
    return trace


class BenchmarkRunner:
    """Framework-agnostic benchmark runner with pluggable adapters."""

    def __init__(
        self,
        task: TaskConfig,
        catalog: ToolCatalog,
        model: str = "sonnet",
        framework: str = "clawteam",
        harness: str = "claude",
        trace_writer: TraceWriter | None = None,
        max_turns: int = 30,
        workspace_manager: WorkspaceManager | None = None,
        bank_factory: Any = None,
        agent_timeout: int = 300,
        judge_model: str = "gpt-5.4",
        skip_judge: bool = False,
        judge_workers: int = 4,
        perturbation: PerturbationConfig | None = None,
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
        self._judge_model = judge_model
        self._skip_judge = skip_judge
        self._judge_workers = judge_workers
        self._perturbation = perturbation
        self._bank: Any = None
        self._workspace: Workspace | None = None

    def _bank_source(self) -> str:
        bank_factory = self._bank_factory
        module_name = getattr(bank_factory, "__module__", "")
        if module_name.startswith("single_agent."):
            return "single_agent"
        return "multi_agent"

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
        completion = CompletionEvaluation(
            task_completion_score=0.0,
        )
        run_error: str | None = None
        run_warnings: list[str] = []
        sde_workspace_checked = False
        workspace_path_for_trace: str | None = None

        if self.task.fixture and self._workspace_manager:
            self._workspace = self._workspace_manager.create(self.task.fixture, run_id)

        try:
            self.close_bank()
            self._bank = self._bank_factory(self.task.domain)
            if self._bank is not None and hasattr(self._bank, "apply_task_metadata"):
                self._bank.apply_task_metadata(self.task.metadata)
            active_task = apply_task_perturbation(self.task, self._perturbation)

            if trace_writer:
                trace_writer.write_event(
                    TraceStart(
                        trace_id=run_id,
                        task_id=active_task.task_id,
                        domain=active_task.domain,
                        framework=self.framework,
                        model=self.model,
                        agents=[
                            AgentInfo(
                                agent_id=agent_cfg.role,
                                role=agent_cfg.role,
                                useful_tools=agent_cfg.useful_tools,
                            )
                            for agent_cfg in active_task.agents
                        ],
                        n_rules=len(active_task.access_rules),
                        perturbation=(
                            self._perturbation.trace_metadata()
                            if self._perturbation else None
                        ),
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
                    task=active_task,
                    catalog=self.catalog,
                    model=self.model,
                    max_turns=self.max_turns,
                    bank=self._bank,
                    workspace=self._workspace,
                    action_sink=action_sink,
                    bank_source=self._bank_source(),
                    perturbation=self._perturbation,
                ),
                build_task_input(active_task),
            )
            final_text = outcome.final_output
            run_error = outcome.error
            if self._workspace is not None:
                workspace_path_for_trace = str(self._workspace.path)
                run_warnings.extend(
                    self._sde_workspace_warnings(action_sink.actions, self._workspace)
                )
                sde_workspace_checked = True
            if self._bank is not None and hasattr(self._bank, "reconcile_trace_actions"):
                self._bank.reconcile_trace_actions(action_sink.actions)
            completion = await self._evaluate_completion(action_sink.actions, final_text)
        finally:
            if self._workspace and self._workspace_manager:
                if workspace_path_for_trace is None:
                    workspace_path_for_trace = str(self._workspace.path)
                if not sde_workspace_checked:
                    run_warnings.extend(
                        self._sde_workspace_warnings(action_sink.actions, self._workspace)
                    )
                keep = (
                    self.task.domain == SDE_DOMAIN
                    or completion.task_completion_score < 1.0
                    or os.getenv("MASP_KEEP_WORKSPACES") == "1"
                )
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
            completion=completion,
            error=run_error,
            warnings=run_warnings,
            perturbation=(
                self._perturbation.trace_metadata()
                if self._perturbation else None
            ),
            workspace_path=workspace_path_for_trace,
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

    def _sde_workspace_warnings(
        self,
        actions: list[ObservableAction],
        workspace: Workspace,
    ) -> list[str]:
        if self.task.domain != SDE_DOMAIN:
            return []

        has_write_source_code = any(
            action.surface == "tool_call"
            and action.tool_name == "write_source_code"
            for action in actions
        )
        if has_write_source_code:
            return []

        try:
            status = subprocess.run(
                ["git", "-C", str(workspace.path), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception as exc:
            return [f"SDE workspace dirty-check failed: {exc}"]

        if status.returncode != 0:
            stderr = status.stderr.strip()
            detail = f": {stderr}" if stderr else ""
            return [f"SDE workspace dirty-check failed{detail}"]

        if status.stdout.strip():
            return [
                "SDE backend workspace changed but trace contains no "
                "write_source_code tool call."
            ]
        return []

    async def _evaluate_completion(
        self,
        actions: list[ObservableAction],
        final_output: str,
    ) -> CompletionEvaluation:
        """Evaluate L2 TCR completion checkpoints from task config."""
        return await evaluate_completion_checkpoints(
            self.task,
            actions,
            final_output,
            bank=self._bank,
            workspace=self._workspace,
            judge_model=self._judge_model,
            skip_judge=self._skip_judge,
            judge_workers=self._judge_workers,
        )


__all__ = [
    "BenchmarkRunner",
    "build_task_input",
    "framework_run_id_slug",
    "next_run_id",
]
