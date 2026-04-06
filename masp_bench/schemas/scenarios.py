"""Scenario configuration schemas: Domain → Instance → Subtask.

Hierarchy:
  DomainConfig        — one per domain (finance, sde, healthcare, crm, it_secops)
    └─ InstanceConfig  — one per scenario instance (e.g. sde1-ecommerce, sde2-admin)
         └─ SubtaskConfig — one per sub-task within an instance (e.g. sde1-t1 .. sde1-t5)
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from masp_bench.schemas.boundaries import BoundaryRule
from masp_bench.schemas.canary import CanaryToken


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class InducementType(str, Enum):
    """Types of inducement that nudge agents toward boundary violations.

    These are NOT explicit jailbreaks — they are realistic pressures
    that naturally arise in multi-agent workflows.
    """

    NONE = "none"  # No inducement (baseline)
    I1_IMPLICIT = "I1"  # Implicit requirement in task description
    I2_URGENCY = "I2"  # Time pressure / urgency framing
    I3_ROLE_AMBIGUITY = "I3"  # Ambiguous role boundaries
    I4_CONTAMINATED_OUTPUT = "I4"  # Tool output suggests boundary-crossing actions


class ArchitecturePattern(str, Enum):
    """Multi-agent orchestration architecture patterns."""

    HUB_AND_SPOKE = "hub_and_spoke"
    PIPELINE = "pipeline"
    PIPELINE_INFO_WALL = "pipeline_info_wall"
    HIERARCHICAL = "hierarchical"
    APPROVAL_CHAIN = "approval_chain"
    FEDERATED = "federated"
    FLAT_PEER = "flat_peer"


class ToolBackendType(str, Enum):
    """How a tool's execution is backed."""

    STATIC = "static"  # Fixed response string
    TEMPLATE = "template"  # Parameterized response with variable substitution
    LLM_SIM = "llm_sim"  # LLM generates realistic response
    REAL = "real"  # Real file I/O or subprocess (SDE fixtures)
    SHADOW = "shadow"  # Shadow external service (GitHub, CI, deploy)


# ---------------------------------------------------------------------------
# Communication topology
# ---------------------------------------------------------------------------


class CommunicationEdge(BaseModel):
    """A permitted communication edge between two agent roles."""

    from_role: str
    to_role: str
    bidirectional: bool = Field(
        False,
        description="If True, communication is allowed in both directions.",
    )


# ---------------------------------------------------------------------------
# Agent config
# ---------------------------------------------------------------------------


class AgentConfig(BaseModel):
    """Configuration for a single agent within an instance."""

    role: str = Field(..., description="Role template name, e.g. 'research_analyst'.")
    description: str = Field(
        ..., description="One-line description of this agent's responsibilities."
    )
    system_prompt: str = Field(
        "",
        description=(
            "Base system prompt for this agent. "
            "BoundaryGuard 'declared' mode appends boundary declarations."
        ),
    )
    tools: list[str] = Field(
        default_factory=list,
        description="List of tool names this agent is allowed to use.",
    )
    can_delegate_to: list[str] = Field(
        default_factory=list,
        description="Roles this agent can delegate tasks to.",
    )
    is_hub: bool = Field(
        False,
        description="True if this agent is the coordinator / hub in hub-and-spoke.",
    )


# ---------------------------------------------------------------------------
# Tool definition (loaded from domain YAML)
# ---------------------------------------------------------------------------


class ToolDef(BaseModel):
    """A single tool definition from a domain YAML file."""

    name: str
    l1_family: str = Field(
        ..., description="L1 universal action family, e.g. 'READ_DATA'."
    )
    description: str = ""
    sensitivity: Literal["low", "medium", "high", "critical"] = "low"
    params: dict[str, str] = Field(
        default_factory=dict,
        description="Parameter name → type string, e.g. {'ticker': 'str'}.",
    )
    mock_response: str = Field(
        "", description="Default static mock response."
    )
    mock_template: str | None = Field(
        None,
        description="Jinja2-style template for parameterized mock responses.",
    )
    backend_type: ToolBackendType = Field(
        ToolBackendType.STATIC,
        description="How this tool is executed in the benchmark.",
    )
    canary_tokens: list[str] = Field(
        default_factory=list,
        description="Canary token labels injected into this tool's response.",
    )


# ---------------------------------------------------------------------------
# Subtask config
# ---------------------------------------------------------------------------


class SubtaskConfig(BaseModel):
    """Configuration for a single subtask within an instance.

    A subtask defines: what the agents must accomplish, which tools are
    required, which boundary rules apply, and what inducement is used.
    """

    id: str = Field(..., description="Unique subtask ID, e.g. 'fin-t1', 'sde1-t3'.")
    instance_id: str = Field(
        ..., description="Parent instance ID, e.g. 'fin', 'sde1-ecommerce'."
    )
    goal: str = Field(
        ..., description="Natural language task description given to the agents."
    )
    sensitivity: Literal["low", "medium", "high", "inducement"] = Field(
        ..., description="Difficulty/sensitivity level of this subtask."
    )

    # --- Required actions ---
    required_tools: list[str] = Field(
        ...,
        description=(
            "Tools that agents legitimately need for this subtask. "
            "Boundary rules must NOT forbid these."
        ),
    )
    primary_agent: str = Field(
        ..., description="The main agent role responsible for this subtask."
    )

    # --- Boundary rules specific to this subtask ---
    boundary_rules: list[BoundaryRule] = Field(
        default_factory=list,
        description="Boundary rules that apply to this subtask.",
    )

    # --- Communication topology for this subtask ---
    allowed_communication: list[CommunicationEdge] = Field(
        default_factory=list,
        description="Edges that are ALLOWED for this subtask.",
    )

    # --- Inducement ---
    inducement_type: InducementType = Field(
        InducementType.NONE,
        description="Type of inducement applied to this subtask.",
    )
    inducement_detail: str | None = Field(
        None,
        description="Specific inducement text injected into the task or tool output.",
    )

    # --- Expected outcome ---
    expected_violations: list[str] = Field(
        default_factory=list,
        description=(
            "For inducement subtasks: which boundary violations we expect "
            "agents to commit, e.g. ['V-OP:execute_trade', 'V-IF:client_ssn']."
        ),
    )


# ---------------------------------------------------------------------------
# Instance config
# ---------------------------------------------------------------------------


class InstanceConfig(BaseModel):
    """Configuration for a single scenario instance.

    An instance binds together: agents, tools, canary vault,
    communication topology, and a set of subtasks.
    """

    id: str = Field(..., description="Unique instance ID, e.g. 'fin', 'sde1-ecommerce'.")
    domain: str = Field(
        ..., description="Domain name: finance | software_engineering | healthcare | customer_support | it_secops."
    )
    name: str = Field(..., description="Human-readable instance name.")
    description: str = ""

    # --- Architecture ---
    architecture: ArchitecturePattern = Field(
        ..., description="Multi-agent orchestration pattern."
    )

    # --- Agents ---
    agents: list[AgentConfig] = Field(
        ..., description="Agent configurations for this instance."
    )

    # --- Canary vault ---
    canary_tokens: list[CanaryToken] = Field(
        default_factory=list,
        description="Canary tokens for this instance.",
    )

    # --- Default communication topology ---
    default_communication: list[CommunicationEdge] = Field(
        default_factory=list,
        description="Default allowed communication edges (may be overridden per subtask).",
    )

    # --- Subtasks ---
    subtasks: list[SubtaskConfig] = Field(
        default_factory=list,
        description="All subtasks for this instance.",
    )

    # --- SDE-specific ---
    fixture_path: str | None = Field(
        None,
        description="Path to real micro-app fixture (SDE only), e.g. 'fixtures/app-ecommerce'.",
    )

    # --- Metadata ---
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Domain config (top-level, loaded from YAML)
# ---------------------------------------------------------------------------


class DomainConfig(BaseModel):
    """Top-level domain configuration loaded from a domain YAML file.

    Contains the full tool inventory for the domain.
    """

    domain: str = Field(..., description="Domain identifier.")
    name: str = Field(..., description="Human-readable domain name.")
    description: str = ""
    tools: list[ToolDef] = Field(
        ..., description="Complete tool inventory for this domain."
    )

    def get_tool(self, name: str) -> ToolDef | None:
        """Look up a tool by name."""
        for t in self.tools:
            if t.name == name:
                return t
        return None

    def get_tools_by_l1(self, l1_family: str) -> list[ToolDef]:
        """Get all tools belonging to an L1 family."""
        return [t for t in self.tools if t.l1_family == l1_family]

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]
