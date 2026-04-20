"""Framework adapter registry for MASP-Bench runner."""

from __future__ import annotations

from .base import (
    CommunicationInterceptionDecision,
    DefaultDefenseHook,
    DefenseHook,
    DefenseMode,
    MASFrameworkAdapter,
    RunContext,
    RunOutcome,
    ToolInterceptionDecision,
)
from .clawteam_adapter import ClawTeamAdapter


def get_framework_adapter(framework: str, **kwargs) -> MASFrameworkAdapter:
    """Return adapter instance by framework identifier."""
    if framework == ClawTeamAdapter.framework_id:
        return ClawTeamAdapter(**kwargs)
    raise ValueError(
        f"Unsupported framework '{framework}'. "
        f"Choose: {ClawTeamAdapter.framework_id}"
    )


__all__ = [
    "ClawTeamAdapter",
    "CommunicationInterceptionDecision",
    "DefaultDefenseHook",
    "DefenseHook",
    "DefenseMode",
    "MASFrameworkAdapter",
    "RunContext",
    "RunOutcome",
    "ToolInterceptionDecision",
    "get_framework_adapter",
]
