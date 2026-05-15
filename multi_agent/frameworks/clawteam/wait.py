"""Wait result models for ClawTeam adapter execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class TeamRuntimeWaitResult:
    status: str
    elapsed: float
    total_tasks: int
    completed_tasks: int
    in_progress_tasks: int
    pending_tasks: int
    blocked_tasks: int
    messages_received: int
    sentinel_completed: bool


@dataclass(slots=True)
class SpawnedAgentProcess:
    role: str
    pid: int | None = None


def _status_value(status: Any) -> Any:
    value = getattr(status, "value", None)
    return status if value is None else value
