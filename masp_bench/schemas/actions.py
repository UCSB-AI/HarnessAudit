"""Observable actions and the two-layer action ontology.

Every agent behavior across all frameworks is normalized into an ObservableAction.
L1 families enable cross-domain comparison; L2 is the domain-specific tool name.
"""

from __future__ import annotations

from datetime import datetime, UTC
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Action Surface — the 4 observable surfaces where agent behavior occurs
# ---------------------------------------------------------------------------


class ActionSurface(str, Enum):
    """The surface on which an agent action is observed."""

    TOOL_CALL = "tool_call"  # agent invokes an external tool
    DELEGATION = "delegation"  # agent delegates a task to another agent
    INTER_AGENT_MSG = "inter_agent_msg"  # message between agents
    FINAL_OUTPUT = "final_output"  # final response to the user


# ---------------------------------------------------------------------------
# L1 Action Family — universal categories for cross-domain comparison
# ---------------------------------------------------------------------------


class L1ActionFamily(str, Enum):
    """Universal action family (Layer 1).

    Every domain-specific tool (L2) maps to exactly one L1 family,
    enabling cross-domain statistical comparison such as:
    "EXECUTE_OPERATION overreach rate is X% in Finance vs Y% in SDE".
    """

    READ_DATA = "READ_DATA"  # read records / files / data
    SEARCH_QUERY = "SEARCH_QUERY"  # search / query / lookup
    ANALYZE_COMPUTE = "ANALYZE_COMPUTE"  # analysis / computation / stats
    WRITE_MODIFY = "WRITE_MODIFY"  # write / modify data or code
    EXECUTE_OPERATION = "EXECUTE_OPERATION"  # irreversible ops (trade, deploy, restart)
    SEND_MESSAGE = "SEND_MESSAGE"  # send message to another agent
    GENERATE_OUTPUT = "GENERATE_OUTPUT"  # produce final output for user


# ---------------------------------------------------------------------------
# Observable Action — the cross-framework normalized behavior unit
# ---------------------------------------------------------------------------


class ObservableAction(BaseModel):
    """A single observable agent action, normalized across frameworks.

    This is the fundamental unit of the Boundary Audit Protocol (BAP).
    Framework-specific adapters convert native events into this schema.
    """

    # --- Identity & ordering ---
    timestamp: datetime = Field(default_factory=datetime.now(UTC))
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
        None, description="L2 domain-specific tool name"
    )
    tool_args: dict[str, Any] | None = Field(
        None, description="Arguments passed to the tool"
    )
    tool_result: str | None = Field(
        None, description="Tool return value (may be truncated)"
    )

    # --- Communication fields (DELEGATION / INTER_AGENT_MSG / FINAL_OUTPUT) ---
    target_agent: str | None = Field(
        None, description="Recipient agent_id (for delegation/message)"
    )
    content: str | None = Field(
        None, description="Message body or final output text"
    )

    # --- L1 annotation (auto-derived from tool_name → domain YAML) ---
    l1_family: L1ActionFamily | None = Field(
        None,
        description="L1 universal action family, auto-populated by BAP adapter",
    )

    # --- Framework provenance ---
    framework: str | None = Field(
        None, description="Source framework: 'openai_agents' | 'crewai' | 'autogen'"
    )
    raw_event: dict[str, Any] | None = Field(
        None, description="Original framework-native event (for debugging)"
    )

    class Config:
        use_enum_values = True
