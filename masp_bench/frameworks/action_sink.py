"""Action capture sink that emits both normalized actions and JSONL trace events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from masp_bench.schemas.actions import ActionSurface, ObservableAction
from masp_bench.trace.events import CommunicationEvent, ToolCallEvent
from masp_bench.trace.writer import TraceWriter


@dataclass
class _PendingAction:
    """Buffered action waiting for global chronological resequencing."""

    ordinal: int
    timestamp: datetime
    surface: ActionSurface
    agent_id: str
    agent_role: str
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: str | None = None
    target_agent: str | None = None
    target_role: str | None = None
    content: str | None = None
    raw_event: dict[str, Any] | None = None


def _coerce_timestamp(value: str | datetime | None) -> datetime:
    """Normalize source timestamps from harness logs into aware UTC datetimes."""

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    else:
        dt = datetime.now(timezone.utc)

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class ActionSink:
    """Collect normalized actions, then resequence them by source timestamp."""

    def __init__(
        self,
        *,
        run_id: str,
        trace_writer: TraceWriter | None = None,
    ) -> None:
        self.run_id: str = run_id
        self.trace_writer: TraceWriter | None = trace_writer
        self._actions: list[ObservableAction] = []
        self._pending_actions: list[_PendingAction] = []
        self._sequence = 0
        self._ordinal = 0

    @property
    def actions(self) -> list[ObservableAction]:
        self.finalize()
        return self._actions

    def reserve_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _reserve_ordinal(self) -> int:
        self._ordinal += 1
        return self._ordinal

    @staticmethod
    def _sort_key(action: _PendingAction) -> tuple[datetime, str, int, int]:
        raw_event = action.raw_event or {}
        return (
            action.timestamp,
            str(raw_event.get("source_file", "")),
            int(raw_event.get("source_line", 0) or 0),
            action.ordinal,
        )

    def finalize(self) -> None:
        """Materialize buffered actions in globally chronological order."""

        if not self._pending_actions:
            return

        for pending in sorted(self._pending_actions, key=self._sort_key):
            seq = self.reserve_sequence()
            action = ObservableAction(
                timestamp=pending.timestamp,
                sequence_num=seq,
                run_id=self.run_id,
                surface=pending.surface,
                agent_id=pending.agent_id,
                agent_role=pending.agent_role,
                tool_name=pending.tool_name,
                tool_args=pending.tool_args,
                tool_result=(
                    pending.tool_result[:500]
                    if pending.tool_result is not None
                    else None
                ),
                target_agent=pending.target_agent,
                target_role=pending.target_role,
                content=pending.content,
                raw_event=pending.raw_event or {},
            )
            self._actions.append(action)
            if self.trace_writer:
                timestamp = pending.timestamp.isoformat()
                if pending.surface == ActionSurface.TOOL_CALL:
                    self.trace_writer.write_event(
                        ToolCallEvent(
                            trace_id=self.run_id,
                            sequence_num=seq,
                            agent_id=pending.agent_id,
                            agent_role=pending.agent_role,
                            tool_name=pending.tool_name or "",
                            tool_args=pending.tool_args or {},
                            tool_result=(pending.tool_result or "")[:500],
                            timestamp=timestamp,
                        )
                    )
                else:
                    self.trace_writer.write_event(
                        CommunicationEvent(
                            trace_id=self.run_id,
                            sequence_num=seq,
                            agent_id=pending.agent_id,
                            agent_role=pending.agent_role,
                            target_agent=pending.target_agent or "",
                            target_role=pending.target_role or "",
                            content=pending.content or "",
                            timestamp=timestamp,
                        )
                    )

        self._pending_actions.clear()

    def emit_tool_call(
        self,
        *,
        agent_id: str,
        agent_role: str,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_result: str,
        raw_event: dict[str, Any] | None = None,
        timestamp: str | datetime | None = None,
        sequence_num: int | None = None,
    ) -> int:
        del sequence_num
        self._pending_actions.append(
            _PendingAction(
                ordinal=self._reserve_ordinal(),
                timestamp=_coerce_timestamp(timestamp),
                surface=ActionSurface.TOOL_CALL,
                agent_id=agent_id,
                agent_role=agent_role,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=tool_result[:500],
                raw_event=raw_event or {},
            )
        )
        return self._ordinal

    def emit_communication(
        self,
        *,
        agent_id: str,
        agent_role: str,
        target_agent: str,
        target_role: str,
        content: str,
        raw_event: dict[str, Any] | None = None,
        timestamp: str | datetime | None = None,
        sequence_num: int | None = None,
    ) -> int:
        del sequence_num
        self._pending_actions.append(
            _PendingAction(
                ordinal=self._reserve_ordinal(),
                timestamp=_coerce_timestamp(timestamp),
                surface=ActionSurface.COMMUNICATION,
                agent_id=agent_id,
                agent_role=agent_role,
                target_agent=target_agent,
                target_role=target_role,
                content=content,
                raw_event=raw_event or {},
            )
        )
        return self._ordinal
