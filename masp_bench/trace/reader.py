"""JSONL trace reader with type-based partitioning and action reconstruction."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from .events import (
    AccessDecisionEvent,
    CanaryLeakEvent,
    CommunicationEvent,
    HandoffEvent,
    ToolCallEvent,
    TraceEnd,
    TraceStart,
)

_EVENT_MAP: dict[str, type] = {
    "trace_start": TraceStart,
    "tool_call": ToolCallEvent,
    "handoff": HandoffEvent,
    "communication": CommunicationEvent,
    "access_decision": AccessDecisionEvent,
    "canary_leak": CanaryLeakEvent,
    "trace_end": TraceEnd,
}


def read_events(path: str | Path) -> Iterator[
    TraceStart | ToolCallEvent | HandoffEvent | CommunicationEvent
    | AccessDecisionEvent | CanaryLeakEvent | TraceEnd
]:
    """Parse each JSONL line by its ``type`` discriminator field."""
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            event_type = raw.get("type")
            cls = _EVENT_MAP.get(event_type)  # type: ignore[arg-type]
            if cls is None:
                raise ValueError(f"Unknown trace event type: {event_type!r}")
            yield cls.model_validate(raw)


class LoadedTrace(NamedTuple):
    """Partitioned trace events for convenient access."""

    start: TraceStart
    tool_calls: list[ToolCallEvent]
    handoffs: list[HandoffEvent]
    communications: list[CommunicationEvent]
    access_decisions: list[AccessDecisionEvent]
    canary_leaks: list[CanaryLeakEvent]
    end: TraceEnd | None


def load_trace(path: str | Path) -> LoadedTrace:
    """Load a full trace file and partition by event type.

    Returns a LoadedTrace NamedTuple with typed lists for each event category.
    """
    start: TraceStart | None = None
    tool_calls: list[ToolCallEvent] = []
    handoffs: list[HandoffEvent] = []
    communications: list[CommunicationEvent] = []
    access_decisions: list[AccessDecisionEvent] = []
    canary_leaks: list[CanaryLeakEvent] = []
    end: TraceEnd | None = None

    for event in read_events(path):
        match event:
            case TraceStart():
                start = event
            case ToolCallEvent():
                tool_calls.append(event)
            case HandoffEvent():
                handoffs.append(event)
            case CommunicationEvent():
                communications.append(event)
            case AccessDecisionEvent():
                access_decisions.append(event)
            case CanaryLeakEvent():
                canary_leaks.append(event)
            case TraceEnd():
                end = event

    if start is None:
        raise ValueError(f"No TraceStart event found in {path}")

    return LoadedTrace(
        start=start,
        tool_calls=tool_calls,
        handoffs=handoffs,
        communications=communications,
        access_decisions=access_decisions,
        canary_leaks=canary_leaks,
        end=end,
    )


def reconstruct_actions(loaded: LoadedTrace) -> list:
    """Convert trace events back to ObservableAction objects for offline re-evaluation.

    Returns a list of ObservableAction sorted by sequence_num.
    """
    from masp_bench.schemas.actions import ObservableAction

    actions: list[ObservableAction] = []

    for tc in loaded.tool_calls:
        actions.append(ObservableAction(
            sequence_num=tc.sequence_num,
            run_id=tc.trace_id,
            surface="tool_call",
            agent_id=tc.agent_id,
            agent_role=tc.agent_role,
            tool_name=tc.tool_name,
            tool_args=tc.tool_args,
            tool_result=tc.tool_result,
        ))

    for ho in loaded.handoffs:
        actions.append(ObservableAction(
            sequence_num=ho.sequence_num,
            run_id=ho.trace_id,
            surface="communication",
            agent_id=ho.from_agent_id,
            agent_role=ho.from_role,
            target_agent=ho.to_agent_id,
            target_role=ho.to_role,
            content=f"[handoff from {ho.from_role} to {ho.to_role}]",
        ))

    for comm in loaded.communications:
        actions.append(ObservableAction(
            sequence_num=comm.sequence_num,
            run_id=comm.trace_id,
            surface="communication",
            agent_id=comm.agent_id,
            agent_role=comm.agent_role,
            target_agent=comm.target_agent,
            target_role=comm.target_role,
            content=comm.content,
        ))

    actions.sort(key=lambda a: a.sequence_num)
    return actions
