"""Observable actions normalized across frameworks.

Every agent behavior across all frameworks is normalized into an ObservableAction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---- Action Surface — 2 observable surfaces, aligned with Access Control branches ----


class ActionSurface(str, Enum):
    """The surface on which an agent action is observed.

    Directly aligned with the two Access Control branches:
      TOOL_CALL     → Operation Access detection
      COMMUNICATION → Information Access detection
    """

    TOOL_CALL = "tool_call"  # agent invokes an external tool
    COMMUNICATION = "communication"  # agent communicates (inter-agent msg, delegation, or user output)


# ---- Observable Action — the cross-framework normalized behavior unit ----


class ObservableAction(BaseModel):
    """A single observable agent action, normalized across frameworks.

    This is the fundamental unit of the Access Audit Protocol (AAP).
    Framework-specific adapters convert native events into this schema.
    """

    # ---- Identity & ordering ----
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Source-observed timestamp for this action when available",
    )
    sequence_num: int = Field(
        ..., description="Monotonically increasing action counter within a run"
    )
    run_id: str = Field(..., description="Unique identifier for this benchmark run")

    # ---- Surface & agent ----
    surface: ActionSurface
    agent_id: str = Field(..., description="Runtime agent identifier")
    agent_role: str = Field(
        ..., description="Role template name (e.g. 'research_analyst')"
    )

    # ---- Tool call fields (populated when surface == TOOL_CALL) ----
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: str | None = None

    # ---- Communication fields (populated when surface == COMMUNICATION) ----
    target_agent: str | None = None
    target_role: str | None = None
    content: str | None = None

    # ---- Framework provenance ----
    raw_event: dict[str, Any] | None = None

    model_config = {"use_enum_values": True}
