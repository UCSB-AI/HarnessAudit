"""Core Pydantic schemas for MASP-Bench."""

from masp_bench.schemas.actions import (
    ActionSurface,
    ObservableAction,
)
from masp_bench.schemas.access_rules import (
    AccessDecision,
    AccessMatch,
    AccessRule,
    AccessType,
    SEVERITY_PENALTIES,
    ViolationCode,
)
from masp_bench.schemas.task import (
    AgentConfig,
    TaskConfig,
    ToolCatalog,
    ToolDef,
)
from masp_bench.schemas.trace import (
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
