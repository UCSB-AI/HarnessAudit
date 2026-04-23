"""Framework adapter base contracts for execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from masp_bench.schemas.task import TaskConfig, ToolCatalog


@dataclass(slots=True)
class RunContext:
    """Shared runtime context passed into framework adapters."""

    run_id: str
    task: TaskConfig
    catalog: ToolCatalog
    model: str
    max_turns: int
    bank: Any
    workspace: Any
    action_sink: Any
    bank_source: str = "masp"


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
