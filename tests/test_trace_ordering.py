from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import importlib.util
import inspect
import json
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _bootstrap_real_modules():
    masp_pkg = types.ModuleType("masp_bench")
    masp_pkg.__path__ = [str(REPO_ROOT / "masp_bench")]
    sys.modules["masp_bench"] = masp_pkg

    schemas_pkg = types.ModuleType("masp_bench.schemas")
    schemas_pkg.__path__ = [str(REPO_ROOT / "masp_bench" / "schemas")]
    sys.modules["masp_bench.schemas"] = schemas_pkg

    trace_pkg = types.ModuleType("masp_bench.trace")
    trace_pkg.__path__ = [str(REPO_ROOT / "masp_bench" / "trace")]
    sys.modules["masp_bench.trace"] = trace_pkg

    frameworks_pkg = types.ModuleType("masp_bench.frameworks")
    frameworks_pkg.__path__ = [str(REPO_ROOT / "masp_bench" / "frameworks")]
    sys.modules["masp_bench.frameworks"] = frameworks_pkg

    actions_mod = types.ModuleType("masp_bench.schemas.actions")

    class ActionSurface:
        TOOL_CALL = "tool_call"
        COMMUNICATION = "communication"

    @dataclass
    class ObservableAction:
        timestamp: datetime
        sequence_num: int
        run_id: str
        surface: str
        agent_id: str
        agent_role: str
        tool_name: str | None = None
        tool_args: dict | None = None
        tool_result: str | None = None
        target_agent: str | None = None
        target_role: str | None = None
        content: str | None = None
        raw_event: dict | None = None

    actions_mod.ActionSurface = ActionSurface
    actions_mod.ObservableAction = ObservableAction
    sys.modules["masp_bench.schemas.actions"] = actions_mod

    events_mod = types.ModuleType("masp_bench.trace.events")

    @dataclass
    class _TraceEvent:
        def model_dump_json(self) -> str:
            return json.dumps(self.__dict__)

    @dataclass
    class ToolCallEvent(_TraceEvent):
        trace_id: str
        sequence_num: int
        agent_id: str
        agent_role: str
        tool_name: str
        tool_args: dict
        tool_result: str
        timestamp: str
        type: str = "tool_call"

    @dataclass
    class CommunicationEvent(_TraceEvent):
        trace_id: str
        sequence_num: int
        agent_id: str
        agent_role: str
        target_agent: str
        target_role: str
        content: str
        timestamp: str
        type: str = "communication"

    events_mod.ToolCallEvent = ToolCallEvent
    events_mod.CommunicationEvent = CommunicationEvent
    sys.modules["masp_bench.trace.events"] = events_mod

    writer_mod = types.ModuleType("masp_bench.trace.writer")

    class TraceWriter:
        def __init__(self, path: str | Path) -> None:
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = None

        def _ensure_open(self):
            if self._fh is None or self._fh.closed:
                self._fh = open(self.path, "a", encoding="utf-8")
            return self._fh

        def write_event(self, event) -> None:
            fh = self._ensure_open()
            fh.write(event.model_dump_json() + "\n")
            fh.flush()

        def close(self) -> None:
            if self._fh is not None and not self._fh.closed:
                self._fh.close()

    writer_mod.TraceWriter = TraceWriter
    sys.modules["masp_bench.trace.writer"] = writer_mod

    _load_module(
        "masp_bench.frameworks.harness_profiles",
        REPO_ROOT / "masp_bench" / "frameworks" / "harness_profiles.py",
    )
    _load_module(
        "masp_bench.frameworks.jsonl_collector",
        REPO_ROOT / "masp_bench" / "frameworks" / "jsonl_collector.py",
    )
    action_sink_mod = _load_module(
        "masp_bench.frameworks.action_sink",
        REPO_ROOT / "masp_bench" / "frameworks" / "action_sink.py",
    )
    openclaw_mod = _load_module(
        "masp_bench.frameworks.openclaw_ingestor",
        REPO_ROOT / "masp_bench" / "frameworks" / "openclaw_ingestor.py",
    )
    return action_sink_mod, writer_mod, openclaw_mod


action_sink_mod, writer_mod, openclaw_mod = _bootstrap_real_modules()
ActionSink = action_sink_mod.ActionSink
TraceWriter = writer_mod.TraceWriter
collect_openclaw_traces = openclaw_mod.collect_openclaw_traces


def test_action_sink_resequences_by_source_timestamp_and_source_line(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "trace.jsonl"
    sink = ActionSink(run_id="run-1", trace_writer=TraceWriter(trace_path))

    sink.emit_tool_call(
        agent_id="concierge_lead",
        agent_role="concierge_lead",
        tool_name="review_service_option",
        tool_args={"option_id": "A"},
        tool_result="late",
        timestamp="2026-04-22T10:08:04.643000+00:00",
        raw_event={"source_file": "hub.jsonl", "source_line": 9},
    )
    sink.emit_communication(
        agent_id="booking_specialist",
        agent_role="booking_specialist",
        target_agent="concierge_lead",
        target_role="concierge_lead",
        content="earlier report",
        timestamp="2026-04-22T10:07:56.091000+00:00",
        raw_event={"source_file": "booking.jsonl", "source_line": 10},
    )
    sink.emit_tool_call(
        agent_id="concierge_lead",
        agent_role="concierge_lead",
        tool_name="compare_service_plans",
        tool_args={"option_ids": "A,B"},
        tool_result="same-time earlier-line",
        timestamp="2026-04-22T10:08:04.643000+00:00",
        raw_event={"source_file": "hub.jsonl", "source_line": 8},
    )

    assert not trace_path.exists()

    actions = sink.actions

    assert [action.sequence_num for action in actions] == [1, 2, 3]
    assert [action.surface for action in actions] == [
        "communication",
        "tool_call",
        "tool_call",
    ]
    assert [action.timestamp.isoformat() for action in actions] == [
        "2026-04-22T10:07:56.091000+00:00",
        "2026-04-22T10:08:04.643000+00:00",
        "2026-04-22T10:08:04.643000+00:00",
    ]
    assert [action.raw_event["source_line"] for action in actions[1:]] == [8, 9]

    trace_events = [
        json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["sequence_num"] for event in trace_events] == [1, 2, 3]
    assert [event["timestamp"] for event in trace_events] == [
        "2026-04-22T10:07:56.091000+00:00",
        "2026-04-22T10:08:04.643000+00:00",
        "2026-04-22T10:08:04.643000+00:00",
    ]


def test_action_sink_emit_methods_do_not_expose_sequence_num_argument() -> None:
    tool_call_params = inspect.signature(ActionSink.emit_tool_call).parameters
    communication_params = inspect.signature(ActionSink.emit_communication).parameters

    assert "sequence_num" not in tool_call_params
    assert "sequence_num" not in communication_params


def test_collect_openclaw_traces_global_ordering_beats_agent_iteration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_id = "ct-openclaw-claude-sonnet-4-ordercheck"
    concierge_session = tmp_path / "concierge.jsonl"
    booking_session = tmp_path / "booking.jsonl"

    concierge_session.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "message",
                        "timestamp": "2026-04-22T10:08:04.578Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "toolCall",
                                    "id": "send-assignment",
                                    "name": "clawteam__mailbox_send",
                                    "arguments": {
                                        "team_name": f"masp-{run_id}",
                                        "from_agent": "concierge_lead",
                                        "to": "booking_specialist",
                                        "content": "assignment",
                                    },
                                }
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "timestamp": "2026-04-22T10:08:04.662Z",
                        "message": {
                            "role": "toolResult",
                            "toolCallId": "send-assignment",
                            "toolName": "clawteam__mailbox_send",
                            "content": [{"type": "text", "text": '{"type":"message"}'}],
                            "details": {
                                "structuredContent": {
                                    "result": {
                                        "timestamp": "2026-04-22T10:08:04.632972+00:00"
                                    }
                                }
                            },
                            "isError": False,
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    booking_session.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "message",
                        "timestamp": "2026-04-22T10:07:56.063Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "toolCall",
                                    "id": "send-report",
                                    "name": "clawteam__mailbox_send",
                                    "arguments": {
                                        "team_name": f"masp-{run_id}",
                                        "from_agent": "booking_specialist",
                                        "to": "concierge_lead",
                                        "content": "no assignment yet",
                                    },
                                }
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "timestamp": "2026-04-22T10:07:56.091Z",
                        "message": {
                            "role": "toolResult",
                            "toolCallId": "send-report",
                            "toolName": "clawteam__mailbox_send",
                            "content": [{"type": "text", "text": '{"type":"message"}'}],
                            "details": {
                                "structuredContent": {
                                    "result": {
                                        "timestamp": "2026-04-22T10:07:56.075295+00:00"
                                    }
                                }
                            },
                            "isError": False,
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_find(agent_role: str, session_id: str, *, created_after: float = 0.0):
        assert created_after == 0.0
        if agent_role == "concierge_lead":
            return [concierge_session]
        if agent_role == "booking_specialist":
            return [booking_session]
        return []

    monkeypatch.setattr(openclaw_mod, "_find_openclaw_sessions", fake_find)

    sink = ActionSink(run_id=run_id)

    collect_openclaw_traces(
        worktree_map={
            "concierge_lead": tmp_path / "concierge",
            "booking_specialist": tmp_path / "booking",
        },
        catalog_tool_names=set(),
        all_agent_roles=["concierge_lead", "booking_specialist"],
        action_sink=sink,
        hub_role="concierge_lead",
        run_id=run_id,
        spawn_timestamp=0.0,
    )

    actions = sink.actions

    assert [action.agent_role for action in actions] == [
        "booking_specialist",
        "concierge_lead",
    ]
    assert [action.sequence_num for action in actions] == [1, 2]
    assert [action.timestamp.isoformat() for action in actions] == [
        "2026-04-22T10:07:56.075295+00:00",
        "2026-04-22T10:08:04.632972+00:00",
    ]
