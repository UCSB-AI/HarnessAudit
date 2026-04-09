"""Observable actions normalized across frameworks.

Every agent behavior across all frameworks is normalized into an ObservableAction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Action Surface — 2 observable surfaces, aligned with Access Control branches
# ---------------------------------------------------------------------------


class ActionSurface(str, Enum):
    """The surface on which an agent action is observed.

    Directly aligned with the two Access Control branches:
      TOOL_CALL     → Operation Access detection
      COMMUNICATION → Information Access detection
    """

    TOOL_CALL = "tool_call"  # agent invokes an external tool
    COMMUNICATION = "communication"  # agent communicates (inter-agent msg, delegation, or user output)


# ---------------------------------------------------------------------------
# Observable Action — the cross-framework normalized behavior unit
# ---------------------------------------------------------------------------


class ObservableAction(BaseModel):
    """A single observable agent action, normalized across frameworks.

    This is the fundamental unit of the Access Audit Protocol (AAP).
    Framework-specific adapters convert native events into this schema.
    """

    # --- Identity & ordering ---
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sequence_num: int = Field(
        ..., description="Monotonically increasing action counter within a run"
    )
    run_id: str = Field(..., description="Unique identifier for this benchmark run")

    # --- Surface & agent ---
    surface: ActionSurface
    agent_id: str = Field(..., description="Runtime agent identifier")
    agent_role: str = Field(
        ..., description="Role template name (e.g. 'research_analyst')"
    )

    # --- Tool call fields (populated when surface == TOOL_CALL) ---
    tool_name: str | None = Field(
        None, description="Domain-specific tool name"
    )
    tool_args: dict[str, Any] | None = Field(
        None, description="Arguments passed to the tool"
    )
    tool_result: str | None = Field(
        None, description="Tool return value (may be truncated)"
    )

    # --- Communication fields (populated when surface == COMMUNICATION) ---
    target_agent: str | None = Field(
        None,
        description="Recipient agent_id, or 'user' for final output (runtime identifier).",
    )
    target_role: str | None = Field(
        None,
        description="Recipient role name, e.g. 'risk_officer' or 'user'. Used by access checker to match rules.",
    )
    content: str | None = Field(
        None, description="Message body or final output text"
    )

    # --- Framework provenance ---
    framework: str | None = Field(
        None, description="Source framework: 'openai_agents' | 'crewai' | 'autogen' ｜ claw_team"
    )
    raw_event: dict[str, Any] | None = Field(
        None, description="Original framework-native event (for debugging)"
    )

    model_config = {"use_enum_values": True}
