"""Core Pydantic schemas for MASP-Bench."""

from masp_bench.schemas.actions import (
    ActionSurface,
    L1ActionFamily,
    ObservableAction,
)
from masp_bench.schemas.boundaries import (
    BoundaryDecision,
    BoundaryMatch,
    BoundaryRule,
    ViolationCode,
)
from masp_bench.schemas.canary import CanaryToken, CanaryVault
from masp_bench.schemas.scenarios import (
    AgentConfig,
    CommunicationEdge,
    DomainConfig,
    InducementType,
    InstanceConfig,
    SubtaskConfig,
)
from masp_bench.schemas.trace import ExecutionTrace, ScenarioResult

__all__ = [
    # actions
    "ActionSurface",
    "L1ActionFamily",
    "ObservableAction",
    # boundaries
    "BoundaryRule",
    "BoundaryMatch",
    "BoundaryDecision",
    "ViolationCode",
    # canary
    "CanaryToken",
    "CanaryVault",
    # scenarios
    "DomainConfig",
    "InstanceConfig",
    "SubtaskConfig",
    "AgentConfig",
    "CommunicationEdge",
    "InducementType",
    # trace
    "ExecutionTrace",
    "ScenarioResult",
]
