"""Action capture sink that emits both normalized actions and JSONL trace events."""

from __future__ import annotations

from typing import Any

from masp_bench.schemas.actions import ActionSurface, ObservableAction
from masp_bench.trace.events import CommunicationEvent, ToolCallEvent
from masp_bench.trace.writer import TraceWriter


class ActionSink:
    """Collect normalized actions and mirror them to trace JSONL events."""

    def __init__(
        self,
        *,
        run_id: str,
        trace_writer: TraceWriter | None = None,
    ) -> None:
        self.run_id: str = run_id
        self.trace_writer: TraceWriter | None = trace_writer
        self._actions: list[ObservableAction] = []
        self._sequence = 0

    @property
    def actions(self) -> list[ObservableAction]:
        return self._actions

    def reserve_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def emit_tool_call(
        self,
        *,
        agent_id: str,
        agent_role: str,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_result: str,
        raw_event: dict[str, Any] | None = None,
        sequence_num: int | None = None,
    ) -> int:
        seq = sequence_num or self.reserve_sequence()
        action = ObservableAction(
            sequence_num=seq,
            run_id=self.run_id,
            surface=ActionSurface.TOOL_CALL,
            agent_id=agent_id,
            agent_role=agent_role,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result[:500],
            raw_event=raw_event or {},
        )
        self._actions.append(action)
        if self.trace_writer:
            self.trace_writer.write_event(
                ToolCallEvent(
                    trace_id=self.run_id,
                    sequence_num=seq,
                    agent_id=agent_id,
                    agent_role=agent_role,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_result=tool_result[:500],
                )
            )
        return seq

    def emit_communication(
        self,
        *,
        agent_id: str,
        agent_role: str,
        target_agent: str,
        target_role: str,
        content: str,
        raw_event: dict[str, Any] | None = None,
        sequence_num: int | None = None,
        kind: str = "message",
    ) -> int:
        seq = sequence_num or self.reserve_sequence()
        action = ObservableAction(
            sequence_num=seq,
            run_id=self.run_id,
            surface=ActionSurface.COMMUNICATION,
            agent_id=agent_id,
            agent_role=agent_role,
            target_agent=target_agent,
            target_role=target_role,
            content=content,
            raw_event=raw_event or {},
        )
        self._actions.append(action)
        if self.trace_writer:
            self.trace_writer.write_event(
                CommunicationEvent(
                    trace_id=self.run_id,
                    sequence_num=seq,
                    agent_id=agent_id,
                    agent_role=agent_role,
                    target_agent=target_agent,
                    target_role=target_role,
                    content=content,
                )
            )
        return seq
