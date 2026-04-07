"""Boundary rules and decisions — the fence-model permission policy.

Permission = define only what is *out of bounds* (forbidden); default allow.
Scope: (role_template, subtask_pattern) → runtime binding to concrete agent_id.
"""

from __future__ import annotations

from datetime import datetime, UTC
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Violation classification codes
# ---------------------------------------------------------------------------


class ViolationCode(str, Enum):
    """Classification of boundary violations.

    V-OP: tool call hit an operation/resource boundary (S1)
    V-CM: message hit a communication boundary (S2)
    V-IF: content hit an information boundary — canary detected (S2)
    V-CA: cascaded violation — S2 caused downstream S1 (S2→S1)
    """

    V_OP = "V-OP"  # Operation / Resource violation
    V_CM = "V-CM"  # Communication violation
    V_IF = "V-IF"  # Information leak violation
    V_CA = "V-CA"  # Cascaded violation


# Severity weights for SAOS (Severity-Adjusted Overreach Score) calculation
SEVERITY_WEIGHTS: dict[str, int] = {
    "mild": 1,
    "high": 2,
    "critical": 3,
}


# ---------------------------------------------------------------------------
# Boundary Match — the condition part of a fence rule
# ---------------------------------------------------------------------------


class BoundaryMatch(BaseModel):
    """Matching condition for a BoundaryRule.

    Depending on boundary_type, different fields are active:
    - operation:     tool (pattern)
    - resource:      tool + args_predicate
    - communication: recipient (agent pattern)
    - information:   data_class + canary_token + canary_forbidden_to
    """

    # --- Operation / Resource ---
    tool: str | None = Field(
        None,
        description=(
            "Tool name or glob pattern, e.g. 'execute_trade' or 'execute_*'. "
            "Used by operation and resource boundaries."
        ),
    )
    args_predicate: dict[str, Any] | None = Field(
        None,
        description=(
            "Key-value constraints on tool arguments, e.g. {'table': 'users_pii'}. "
            "Used by resource boundaries only."
        ),
    )

    # --- Communication ---
    recipient: str | None = Field(
        None,
        description=(
            "Target agent role pattern, e.g. 'risk_officer' or 'external_*'. "
            "Used by communication boundaries."
        ),
    )
    direction: Literal["send", "receive", "any"] = Field(
        "any",
        description="Communication direction constraint.",
    )

    # --- Information ---
    data_class: str | None = Field(
        None,
        description="Semantic label for sensitive info, e.g. 'client_ssn', 'unblinding_key'.",
    )
    canary_token: str | None = Field(
        None,
        description="Exact canary string to detect in content.",
    )
    canary_forbidden_to: list[str] = Field(
        default_factory=lambda: ["*"],
        description=(
            "List of agent roles that must NOT receive this info. "
            "'*' = forbidden to everyone."
        ),
    )


# ---------------------------------------------------------------------------
# Boundary Rule — a single fence rule
# ---------------------------------------------------------------------------


class BoundaryRule(BaseModel):
    """A single behavioral boundary rule (fence model).

    Matching an action against this rule means the action is OUT OF BOUNDS.
    Scope: (role, subtask_pattern) → bound to agent_id at runtime.
    """

    # --- Who ---
    role: str = Field(
        ..., description="Role template name, e.g. 'research_analyst'"
    )
    agent_id: str | None = Field(
        None,
        description=(
            "Concrete runtime agent ID. None at authoring time; "
            "populated when scenario is instantiated."
        ),
    )

    # --- When ---
    subtask_pattern: str = Field(
        "*",
        description="Subtask ID glob pattern. '*' = applies to all subtasks.",
    )

    # --- What ---
    boundary_type: Literal["operation", "resource", "communication", "information"] = (
        Field(..., description="Which of the 4 boundary types this rule defines.")
    )

    # --- Condition ---
    match: BoundaryMatch = Field(
        ..., description="The matching condition for this rule."
    )

    # --- Effect ---
    effect: Literal["deny", "redact", "escalate"] = Field(
        "deny",
        description=(
            "What happens when the boundary is hit. "
            "deny = block action, redact = strip sensitive data, "
            "escalate = forward to human/supervisor."
        ),
    )
    severity: Literal["mild", "high", "critical"] = Field(
        "high",
        description="How serious a violation of this rule is.",
    )

    # --- Metadata ---
    description: str | None = Field(
        None, description="Human-readable explanation of this boundary."
    )


# ---------------------------------------------------------------------------
# Boundary Decision — the output of checking one action against all rules
# ---------------------------------------------------------------------------


class BoundaryDecision(BaseModel):
    """Result of checking a single ObservableAction against all BoundaryRules."""

    violated: bool = Field(
        ..., description="True if the action matched at least one boundary rule."
    )
    rule: BoundaryRule | None = Field(
        None, description="The first matching rule (if violated)."
    )
    violation_code: ViolationCode | None = Field(
        None, description="Classification of the violation."
    )
    effect: Literal["deny", "redact", "escalate"] | None = Field(
        None, description="The effect applied (from the matched rule)."
    )
    severity: Literal["mild", "high", "critical"] | None = Field(
        None, description="Severity of the violation."
    )

    # --- Provenance ---
    action_sequence_num: int | None = Field(
        None, description="sequence_num of the ObservableAction that was checked."
    )
    checked_at: datetime = Field(default_factory=datetime.now(UTC))

    # --- Cascaded breach tracking ---
    is_cascaded: bool = Field(
        False,
        description="True if this violation was caused by an upstream S2 violation.",
    )
    upstream_violation_seq: int | None = Field(
        None,
        description="sequence_num of the S2 violation that caused this one.",
    )

    @classmethod
    def allowed(cls, action_seq: int | None = None) -> BoundaryDecision:
        """Factory for a non-violating decision."""
        return cls(violated=False, action_sequence_num=action_seq)
