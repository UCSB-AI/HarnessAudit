"""MASP-Bench trace module — JSONL trajectory capture and replay."""

from .events import (
    AccessDecisionEvent,
    AgentInfo,
    CanaryLeakEvent,
    CommunicationEvent,
    HandoffEvent,
    ToolCallEvent,
    TraceEnd,
    TraceEvent,
    TraceStart,
)
from .reader import LoadedTrace, load_trace, read_events, reconstruct_actions
from .writer import TraceWriter

__all__ = [
    "AccessDecisionEvent",
    "AgentInfo",
    "CanaryLeakEvent",
    "CommunicationEvent",
    "HandoffEvent",
    "LoadedTrace",
    "ToolCallEvent",
    "TraceEnd",
    "TraceEvent",
    "TraceStart",
    "TraceWriter",
    "load_trace",
    "read_events",
    "reconstruct_actions",
]
