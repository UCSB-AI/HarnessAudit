"""Core Pydantic schemas for HarnessAudit-Bench."""

from multi_agent.schemas.actions import (
    ActionSurface,
    ObservableAction,
)
from multi_agent.schemas.access_rules import (
    AccessDecision,
    AccessMatch,
    AccessRule,
    AccessType,
    SEVERITY_PENALTIES,
    ViolationCode,
)
from multi_agent.schemas.task import (
    AgentConfig,
    TaskConfig,
    ToolCatalog,
    ToolDef,
)
from multi_agent.schemas.trace import (
    ExecutionTrace,
    RunMetrics,
)

__all__ = [
    # actions
    "ActionSurface",
    "ObservableAction",
    # access rules
    "AccessRule",
    "AccessMatch",
    "AccessDecision",
    "AccessType",
    "SEVERITY_PENALTIES",
    "ViolationCode",
    # task
    "TaskConfig",
    "AgentConfig",
    "ToolCatalog",
    "ToolDef",
    # trace
    "ExecutionTrace",
    "RunMetrics",
]
