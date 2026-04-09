"""Task and tool catalog schemas.

TaskConfig  — the atomic, self-contained benchmark unit (one YAML per task).
ToolCatalog — domain-level tool inventory (loaded from tools/<domain>.yaml).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from masp_bench.schemas.access_rules import AccessRule
from masp_bench.schemas.canary import CanaryToken


# ---------------------------------------------------------------------------
# Tool definition & catalog (loaded from tools/<domain>.yaml)
# ---------------------------------------------------------------------------


class ToolDef(BaseModel):
    """A single tool definition."""

    name: str
    description: str = ""
    sensitivity: int = Field(
        1, ge=1, le=3,
        description="1 = low, 2 = medium, 3 = high.",
    )
    params: dict[str, str] = Field(
        default_factory=dict,
        description="Parameter name → type string, e.g. {'ticker': 'str'}.",
    )
    mock_response: str = Field(
        "", description="Static mock response (fallback when no fixture).",
    )
    fixture: str = Field(
        "",
        description=(
            "Fixture JSON filename (relative to fixtures/{domain}/), "
            "e.g. 'market_data.json'. Keys are lookup values, values are "
            "pre-formatted response strings. Special keys: _header, _footer, _default."
        ),
    )
    fixture_key: str = Field(
        "",
        description=(
            "Which param name to use as the lookup key into the fixture, "
            "e.g. 'tickers' or 'client_id'. Supports comma-separated multi-lookup."
        ),
    )
    backend_type: Literal["static", "template", "real", "shadow"] = Field(
        "static",
        description="How this tool is executed. MVP uses 'static' only.",
    )
    canary_tokens: list[str] = Field(
        default_factory=list,
        description="Canary token labels injected into this tool's response.",
    )

    # Runtime-loaded fixture data (not in YAML, populated by loader)
    fixture_data: dict[str, str] | None = Field(
        None, exclude=True,
        description="Loaded fixture dict. Keys = lookup values, values = response strings.",
    )


class ToolCatalog(BaseModel):
    """Domain-level tool inventory, loaded from tools/<domain>.yaml."""

    domain: str = Field(..., description="Domain identifier, e.g. 'finance'.")
    name: str = Field(..., description="Human-readable domain name.")
    description: str = ""
    tools: list[ToolDef] = Field(
        ..., description="Complete tool inventory for this domain.",
    )

    def get_tool(self, name: str) -> ToolDef | None:
        """Look up a tool by name."""
        for t in self.tools:
            if t.name == name:
                return t
        return None

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]


# ---------------------------------------------------------------------------
# Agent config
# ---------------------------------------------------------------------------


class AgentConfig(BaseModel):
    """Configuration for a single agent within a task."""

    role: str = Field(..., description="Role template name, e.g. 'research_analyst'.")
    description: str = Field(
        ..., description="One-line description of this agent's responsibilities."
    )
    system_prompt: str = Field(
        "",
        description="Base system prompt for this agent.",
    )
    visible_tools: list[str] = Field(
        default_factory=list,
        description=(
            "Tool names visible to this agent (references tools/<domain>.yaml). "
            "This is NOT an access control list — access rules define what's forbidden."
        ),
    )


# ---------------------------------------------------------------------------
# Task config — the atomic, self-contained benchmark unit
# ---------------------------------------------------------------------------


class TaskConfig(BaseModel):
    """A single benchmark task (self-contained).

    One YAML file per task. Contains everything needed to run and evaluate:
    agents, tool references, access rules, canary tokens, and success checks.

    Tool definitions are loaded separately from tools/<domain>.yaml.
    """

    task_id: str = Field(..., description="Unique task ID, e.g. 'fin-t1', 'sde1-t3'.")
    domain: str = Field(
        ..., description="Domain tag: finance | software_engineering | healthcare | customer_support | it_secops.",
    )
    goal: str = Field(
        ..., description="Natural language task description given to the agents.",
    )
    sensitivity: int = Field(
        1, ge=1, le=3,
        description="1 = low, 2 = medium, 3 = high.",
    )

    # --- Agents ---
    agents: list[AgentConfig] = Field(
        ..., description="Agent configurations for this task.",
    )

    # --- Access rules (what's forbidden) ---
    access_rules: list[AccessRule] = Field(
        default_factory=list,
        description="Access control rules. Matching an action = violation.",
    )

    # --- Canary tokens ---
    canary_tokens: list[CanaryToken] = Field(
        default_factory=list,
        description="Canary tokens planted in tool responses for leak detection.",
    )

    # --- Success checks (for TSR computation) ---
    success_checks: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Lightweight checks to determine task completion. "
            "Each dict has a 'type' key and type-specific fields. "
            "Examples: {'type': 'tool_called', 'tool_name': 'get_market_data', 'min_calls': 1}, "
            "{'type': 'final_output_contains', 'keywords': ['AAPL', 'recommendation']}."
        ),
    )

    # --- Metadata (optional, for analysis) ---
    metadata: dict[str, Any] = Field(default_factory=dict)
