"""Framework adapter base contracts for AAP execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Protocol

from masp_bench.schemas.task import TaskConfig, ToolCatalog
from masp_bench.trace.writer import TraceWriter

DefenseMode = Literal["monitor", "declared", "enforced"]


@dataclass(slots=True)
class ToolInterceptionDecision:
    """Decision returned by defense hook before a tool call."""

    allow: bool = True
    override_result: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CommunicationInterceptionDecision:
    """Decision returned by defense hook before a communication action."""

    allow: bool = True
    override_content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class DefenseHook(Protocol):
    """Framework-agnostic policy hook for runtime interception."""

    def before_tool_call(
        self,
        *,
        run_id: str,
        defense_mode: DefenseMode,
        agent_role: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> ToolInterceptionDecision:
        ...

    def before_communication(
        self,
        *,
        run_id: str,
        defense_mode: DefenseMode,
        agent_role: str,
        target_role: str,
        content: str,
        kind: str,
    ) -> CommunicationInterceptionDecision:
        ...


class DefaultDefenseHook:
    """Default no-op policy with mode metadata tagging."""

    def before_tool_call(
        self,
        *,
        run_id: str,
        defense_mode: DefenseMode,
        agent_role: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> ToolInterceptionDecision:
        return ToolInterceptionDecision(
            allow=True,
            metadata={
                "defense_mode": defense_mode,
                "policy_stage": "before_tool_call",
            },
        )

    def before_communication(
        self,
        *,
        run_id: str,
        defense_mode: DefenseMode,
        agent_role: str,
        target_role: str,
        content: str,
        kind: str,
    ) -> CommunicationInterceptionDecision:
        return CommunicationInterceptionDecision(
            allow=True,
            metadata={
                "defense_mode": defense_mode,
                "policy_stage": "before_communication",
                "communication_kind": kind,
            },
        )


@dataclass(slots=True)
class RunContext:
    """Shared runtime context passed into framework adapters."""

    run_id: str
    task: TaskConfig
    catalog: ToolCatalog
    model: str
    framework: str
    defense_mode: DefenseMode
    max_turns: int
    trace_writer: TraceWriter | None
    bank: Any
    workspace: Any
    action_sink: Any
    tool_runtime: Any


@dataclass(slots=True)
class RunOutcome:
    """Adapter execution result consumed by benchmark runner."""

    framework: str
    final_output: str
    error: str | None = None


class MASFrameworkAdapter(Protocol):
    """Framework-specific adapter contract."""

    framework_id: ClassVar[str]

    async def run(self, ctx: RunContext, initial_input: str | list[dict[str, Any]]) -> RunOutcome:
        ...
