"""Framework adapter base contracts for execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from masp_bench.schemas.task import TaskConfig, ToolCatalog
from masp_bench.trace.writer import TraceWriter


@dataclass(slots=True)
class RunContext:
    """Shared runtime context passed into framework adapters."""

    run_id: str
    task: TaskConfig
    catalog: ToolCatalog
    model: str
    framework: str
    harness: str
    max_turns: int
    trace_writer: TraceWriter | None
    bank: Any
    workspace: Any
    action_sink: Any


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
