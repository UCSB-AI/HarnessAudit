"""Framework adapter registry for MASP-Bench runner."""

from __future__ import annotations

from .base import (
    MASFrameworkAdapter,
    RunContext,
    RunOutcome,
)
from .clawteam_adapter import ClawTeamAdapter
from .oai_adapter import OAIAdapter


def get_framework_adapter(framework: str, **kwargs) -> MASFrameworkAdapter:
    """Return adapter instance by framework identifier."""
    if framework == ClawTeamAdapter.framework_id:
        return ClawTeamAdapter(**kwargs)
    if framework == OAIAdapter.framework_id:
        return OAIAdapter(**kwargs)
    raise ValueError(
        f"Unsupported framework '{framework}'. "
        f"Choose: {ClawTeamAdapter.framework_id}, {OAIAdapter.framework_id}"
    )


__all__ = [
    "ClawTeamAdapter",
    "OAIAdapter",
    "MASFrameworkAdapter",
    "RunContext",
    "RunOutcome",
    "get_framework_adapter",
]
