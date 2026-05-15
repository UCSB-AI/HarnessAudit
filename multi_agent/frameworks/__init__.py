"""Framework adapter registry for HarnessAudit-Bench runner."""

from __future__ import annotations

from .core.base import (
    MASFrameworkAdapter,
    RunContext,
    RunOutcome,
)
from .adk.adapter import ADKAdapter
from .clawteam.adapter import ClawTeamAdapter
from .native.openclaw.local_adapter import OpenClawLocalAdapter
from .oai.adapter import OAIAdapter


def get_framework_adapter(framework: str, **kwargs) -> MASFrameworkAdapter:
    """Return adapter instance by framework identifier."""
    if framework == ClawTeamAdapter.framework_id:
        return ClawTeamAdapter(**kwargs)
    if framework == OpenClawLocalAdapter.framework_id:
        return OpenClawLocalAdapter(**kwargs)
    if framework == OAIAdapter.framework_id:
        return OAIAdapter(**kwargs)
    if framework == ADKAdapter.framework_id:
        return ADKAdapter(**kwargs)
    raise ValueError(
        f"Unsupported framework '{framework}'. "
        "Choose: "
        f"{ClawTeamAdapter.framework_id}, "
        f"{OpenClawLocalAdapter.framework_id}, "
        f"{OAIAdapter.framework_id}, "
        f"{ADKAdapter.framework_id}"
    )


__all__ = [
    "ClawTeamAdapter",
    "OpenClawLocalAdapter",
    "OAIAdapter",
    "ADKAdapter",
    "MASFrameworkAdapter",
    "RunContext",
    "RunOutcome",
    "get_framework_adapter",
]
