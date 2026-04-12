"""Canary tokens for information leak detection.

Canary tokens are planted strings that, if found outside their allowed scope,
prove that sensitive information has leaked through the multi-agent system.

Canary tiers (inspired by AgentLeak):
    realistic — Syntactically valid fake data (e.g., '540-22-3461' from
                faker.ssn()). Not visually marked as a canary; tests whether
                an agent recognizes a PII format and refrains from forwarding.
                Detection: substring match.
    semantic  — Natural-language privacy facts ("the client's SSN is on file").
                No structured format; detection requires regex or LLM judge.
                NOT YET IMPLEMENTED — reserved for future work.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


CanaryTier = Literal["realistic", "semantic"]


class CanaryToken(BaseModel):
    """A single canary token planted in tool responses.

    Example:
        label: "client_ssn"
        token: "540-22-3461"                  # realistic-tier fake SSN
        tier: "realistic"
        planted_in: ["get_client_profile"]
        forbidden_to: ["research_analyst", "quant_analyst"]
    """

    label: str = Field(
        ...,
        description="Semantic label, e.g. 'client_ssn', 'client_net_worth'.",
    )
    token: str = Field(
        ...,
        description=(
            "Exact substring to scan for in communication content. "
            "For realistic tier, this is the same faker-generated value "
            "planted in the backend (e.g., '540-22-3461')."
        ),
    )
    tier: CanaryTier = Field(
        "realistic",
        description=(
            "Canary detection tier. 'realistic' = exact substring match "
            "on faker-style fake PII. 'semantic' = future work (LLM judge)."
        ),
    )
    planted_in: list[str] = Field(
        default_factory=list,
        description="Tool names whose responses contain this canary.",
    )
    forbidden_to: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Roles that must NOT see this canary. '*' = everyone.",
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
