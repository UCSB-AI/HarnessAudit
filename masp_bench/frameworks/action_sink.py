"""Action capture sink that emits both normalized actions and JSONL trace events."""

from __future__ import annotations

from typing import Any

from masp_bench.schemas.actions import ActionSurface, ObservableAction
from masp_bench.trace.events import CommunicationEvent, HandoffEvent, ToolCallEvent
from masp_bench.trace.writer import TraceWriter

from .base import (
    CommunicationInterceptionDecision,
    DefenseHook,
    DefenseMode,
)


class ActionSink:
    """Collect normalized actions and mirror them to trace JSONL events."""

    def __init__(
        self,
        *,
        run_id: str,
        defense_mode: DefenseMode,
        trace_writer: TraceWriter | None = None,
        defense_hook: DefenseHook | None = None,
    ) -> None:
        self.run_id: str = run_id
        self.defense_mode: DefenseMode = defense_mode
        self.trace_writer: TraceWriter | None = trace_writer
        self.defense_hook: DefenseHook | None = defense_hook
        self._actions: list[ObservableAction] = []
        self._sequence = 0

    @property
    def actions(self) -> list[ObservableAction]:
        return self._actions

    def reserve_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _merge_raw_event(
        self,
        *,
        raw_event: dict[str, Any] | None,
        defense_meta: dict[str, Any] | None,
        blocked: bool,
    ) -> dict[str, Any]:
        merged = dict(raw_event or {})
        if defense_meta:
            merged.update(defense_meta)
        if blocked:
            merged["defense_blocked"] = True
        return merged

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

    def _intercept_communication(
        self,
        *,
        agent_role: str,
        target_role: str,
        content: str,
        kind: str,
    ) -> tuple[str, dict[str, Any], bool]:
        if not self.defense_hook:
            return content, {}, False

        decision: CommunicationInterceptionDecision = self.defense_hook.before_communication(
            run_id=self.run_id,
            defense_mode=self.defense_mode,
            agent_role=agent_role,
            target_role=target_role,
            content=content,
            kind=kind,
        )
        blocked = not decision.allow
        if blocked and decision.override_content is not None:
            return decision.override_content, decision.metadata, True
        return content, decision.metadata, blocked

    def emit_handoff(
        self,
        *,
        from_agent_id: str,
        from_role: str,
        to_agent_id: str,
        to_role: str,
        handoff_note: str,
        handoff_context_raw: Any,
        raw_event: dict[str, Any] | None = None,
        sequence_num: int | None = None,
    ) -> int:
        content, defense_meta, blocked = self._intercept_communication(
            agent_role=from_role,
            target_role=to_role,
            content=handoff_note,
            kind="handoff",
        )
        merged_raw = self._merge_raw_event(
            raw_event=raw_event,
            defense_meta=defense_meta,
            blocked=blocked,
        )
        if handoff_context_raw is not None:
            merged_raw.setdefault("handoff_context_raw", handoff_context_raw)
        seq = sequence_num or self.reserve_sequence()
        action = ObservableAction(
            sequence_num=seq,
            run_id=self.run_id,
            surface=ActionSurface.COMMUNICATION,
            agent_id=from_agent_id,
            agent_role=from_role,
            target_agent=to_agent_id,
            target_role=to_role,
            content=content,
            raw_event=merged_raw,
        )
        self._actions.append(action)
        if self.trace_writer:
            self.trace_writer.write_event(
                HandoffEvent(
                    trace_id=self.run_id,
                    sequence_num=seq,
                    from_agent_id=from_agent_id,
                    from_role=from_role,
                    to_agent_id=to_agent_id,
                    to_role=to_role,
                    handoff_note=content,
                    handoff_context_raw=handoff_context_raw,
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
        intercepted_content, defense_meta, blocked = self._intercept_communication(
            agent_role=agent_role,
            target_role=target_role,
            content=content,
            kind=kind,
        )
        merged_raw = self._merge_raw_event(
            raw_event=raw_event,
            defense_meta=defense_meta,
            blocked=blocked,
        )
        seq = sequence_num or self.reserve_sequence()
        action = ObservableAction(
            sequence_num=seq,
            run_id=self.run_id,
            surface=ActionSurface.COMMUNICATION,
            agent_id=agent_id,
            agent_role=agent_role,
            target_agent=target_agent,
            target_role=target_role,
            content=intercepted_content,
            raw_event=merged_raw,
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
                    content=intercepted_content,
                )
            )
        return seq
