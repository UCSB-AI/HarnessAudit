"""Framework adapter registry for MASP-Bench runner."""

from __future__ import annotations

from .base import (
    MASFrameworkAdapter,
    RunContext,
    RunOutcome,
)
from .clawteam_adapter import ClawTeamAdapter
from .oai_adapter import OAIAdapter
from .openclaw_local_adapter import OpenClawLocalAdapter


def get_framework_adapter(framework: str, **kwargs) -> MASFrameworkAdapter:
    """Return adapter instance by framework identifier."""
    if framework == ClawTeamAdapter.framework_id:
        return ClawTeamAdapter(**kwargs)
    if framework == OpenClawLocalAdapter.framework_id:
        return OpenClawLocalAdapter(**kwargs)
    if framework == OAIAdapter.framework_id:
        return OAIAdapter(**kwargs)
    raise ValueError(
        f"Unsupported framework '{framework}'. "
        "Choose: "
        f"{ClawTeamAdapter.framework_id}, "
        f"{OpenClawLocalAdapter.framework_id}, "
        f"{OAIAdapter.framework_id}"
    )


__all__ = [
    "ClawTeamAdapter",
    "OpenClawLocalAdapter",
    "OAIAdapter",
    "MASFrameworkAdapter",
    "RunContext",
    "RunOutcome",
    "get_framework_adapter",
]
