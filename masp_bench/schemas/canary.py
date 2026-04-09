"""Canary tokens for information leak detection.

Canary tokens are planted strings that, if found outside their allowed scope,
prove that sensitive information has leaked through the multi-agent system.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class CanaryToken(BaseModel):
    """A single canary token planted in tool responses.

    Example:
        label: "client_ssn"
        token: "CANARY-SSN-987654321"
        planted_in: ["get_client_records"]
        forbidden_to: ["research_analyst", "quant_analyst"]
        sensitivity: 3
    """

    label: str = Field(
        ...,
        description="Semantic label, e.g. 'client_ssn', 'unblinding_key'.",
    )
    token: str = Field(
        ...,
        description="Exact marker string, e.g. 'CANARY-SSN-987654321'.",
    )
    planted_in: list[str] = Field(
        default_factory=list,
        description="Tool names whose mock responses contain this canary.",
    )
    forbidden_to: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Roles that must NOT see this canary. '*' = everyone.",
    )
    sensitivity: int = Field(
        3, ge=1, le=3,
        description="1 = low, 2 = medium, 3 = high.",
    )


class CanaryLeak(BaseModel):
    """Record of a detected canary leak during a benchmark run."""

    canary_label: str
    canary_token: str
    leaked_by_role: str = Field(..., description="Role of the agent that leaked the canary.")
    leaked_to_role: str = Field(..., description="Role of the recipient (used for policy matching).")
    leaked_by_agent: str | None = Field(None, description="Agent ID (runtime, for debugging).")
    leaked_to_agent: str | None = Field(None, description="Recipient agent ID (runtime, for debugging).")
    surface: str = Field(
        ...,
        description="Where the leak was detected: 'communication'.",
    )
    action_sequence_num: int
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
