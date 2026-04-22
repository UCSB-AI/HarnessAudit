"""JSONL trace event types for multi-agent access control evaluation.

Each event is a Pydantic model with a ``type`` literal discriminator field.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Helper models
# ---------------------------------------------------------------------------


class AgentInfo(BaseModel):
    """Lightweight agent descriptor for TraceStart."""

    agent_id: str
    role: str
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="Minimum safe tool set. Other catalog tools are visible but forbidden.",
    )


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class TraceStart(BaseModel):
    """Emitted once at the start of a run."""

    type: Literal["trace_start"] = "trace_start"
    trace_id: str
    task_id: str
    domain: str
    framework: str
    model: str
    defense_mode: str
    agents: list[AgentInfo] = Field(default_factory=list)
    n_rules: int = 0
    timestamp: str = Field(default_factory=_now)


class ToolCallEvent(BaseModel):
    """Emitted for every tool invocation."""

    type: Literal["tool_call"] = "tool_call"
    trace_id: str
    sequence_num: int
    agent_id: str
    agent_role: str
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_result: str = ""
    timestamp: str = Field(default_factory=_now)



class CommunicationEvent(BaseModel):
    """Emitted for inter-agent messages and final output to user."""

    type: Literal["communication"] = "communication"
    trace_id: str
    sequence_num: int
    agent_id: str
    agent_role: str
    target_agent: str
    target_role: str
    content: str = ""
    timestamp: str = Field(default_factory=_now)


class AccessDecisionEvent(BaseModel):
    """Emitted for each action's access check result (post-run)."""

    type: Literal["access_decision"] = "access_decision"
    trace_id: str
    action_sequence_num: int
    violated: bool
    violation_code: str | None = None
    severity: str | None = None
    effect: str | None = None
    rule_description: str | None = None
    timestamp: str = Field(default_factory=_now)


class TraceEnd(BaseModel):
    """Emitted once at the end (always, even on error)."""

    type: Literal["trace_end"] = "trace_end"
    trace_id: str
    n_actions: int = 0
    n_tool_calls: int = 0
    n_communications: int = 0
    n_violations: int = 0
    access_score: float = 0.0
    tsr: float = 0.0
    qsr: float = 0.0
    violations_by_code: dict[str, int] = Field(default_factory=dict)
    wall_time_s: float = 0.0
    error: str | None = None
    timestamp: str = Field(default_factory=_now)
