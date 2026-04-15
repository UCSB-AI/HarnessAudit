"""Framework adapter registry for MASP-Bench runner."""

from __future__ import annotations

from .autogen_adapter import AutoGenAdapter
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
from .openai_adapter import OpenAIAdapter


def get_framework_adapter(framework: str) -> MASFrameworkAdapter:
    """Return adapter instance by framework identifier."""
    if framework == OpenAIAdapter.framework_id:
        return OpenAIAdapter()
    if framework == AutoGenAdapter.framework_id:
        return AutoGenAdapter()
    raise ValueError(
        f"Unsupported framework '{framework}'. "
        f"Choose one of: {OpenAIAdapter.framework_id}, {AutoGenAdapter.framework_id}"
    )


__all__ = [
    "AutoGenAdapter",
    "CommunicationInterceptionDecision",
    "DefaultDefenseHook",
    "DefenseHook",
    "DefenseMode",
    "MASFrameworkAdapter",
    "OpenAIAdapter",
    "RunContext",
    "RunOutcome",
    "ToolInterceptionDecision",
    "get_framework_adapter",
]
