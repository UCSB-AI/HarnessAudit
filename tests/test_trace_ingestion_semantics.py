from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ALL_AGENT_ROLES = [
    "concierge_lead",
    "booking_specialist",
    "vendor_liaison",
    "payments_and_coverage_specialist",
]
SENDER_ROLE = "payments_and_coverage_specialist"
EXPECTED_TEAM = "masp-current"


def _load_module(module_name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _bootstrap_ingestors():
    masp_pkg = types.ModuleType("masp_bench")
    masp_pkg.__path__ = [str(REPO_ROOT / "masp_bench")]
    sys.modules["masp_bench"] = masp_pkg

    frameworks_pkg = types.ModuleType("masp_bench.frameworks")
    frameworks_pkg.__path__ = [str(REPO_ROOT / "masp_bench" / "frameworks")]
    sys.modules["masp_bench.frameworks"] = frameworks_pkg

    harness_profiles = _load_module(
        "masp_bench.frameworks.harness_profiles",
        REPO_ROOT / "masp_bench" / "frameworks" / "harness_profiles.py",
    )
    jsonl_collector = _load_module(
        "masp_bench.frameworks.jsonl_collector",
        REPO_ROOT / "masp_bench" / "frameworks" / "jsonl_collector.py",
    )
    codex_ingestor = _load_module(
        "masp_bench.frameworks.codex_ingestor",
        REPO_ROOT / "masp_bench" / "frameworks" / "codex_ingestor.py",
    )
    openclaw_ingestor = _load_module(
        "masp_bench.frameworks.openclaw_ingestor",
        REPO_ROOT / "masp_bench" / "frameworks" / "openclaw_ingestor.py",
    )
    return harness_profiles, jsonl_collector, codex_ingestor, openclaw_ingestor


HARNESS_PROFILES, JSONL_COLLECTOR, CODEX_INGESTOR, OPENCLAW_INGESTOR = _bootstrap_ingestors()


class _FakeActionSink:
    def __init__(self) -> None:
        self.communications: list[dict] = []
        self.tool_calls: list[dict] = []

    def emit_communication(self, **kwargs) -> None:
        self.communications.append(kwargs)

    def emit_tool_call(self, **kwargs) -> None:
        self.tool_calls.append(kwargs)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def _normalized_communications(sink: _FakeActionSink) -> list[dict[str, str]]:
    def _normalize_timestamp(value: str) -> str:
        return value[:-1] + "+00:00" if value.endswith("Z") else value

    return [
        {
            "agent_role": entry["agent_role"],
            "target_role": entry["target_role"],
            "content": entry["content"],
            "timestamp": _normalize_timestamp(entry["timestamp"]),
        }
        for entry in sink.communications
    ]


def _assert_semantics_match(expected: list[dict[str, str]], *actual_sets: list[dict[str, str]]) -> None:
    for actual in actual_sets:
        assert actual == expected


def _claude_semantics(records: list[dict], tmp_path: Path) -> list[dict[str, str]]:
    session_path = tmp_path / "claude.jsonl"
    _write_jsonl(session_path, records)
    sink = _FakeActionSink()
    JSONL_COLLECTOR.parse_jsonl(
        session_path,
        SENDER_ROLE,
        set(),
        ALL_AGENT_ROLES,
        sink,
        expected_team_name=EXPECTED_TEAM,
    )
    return _normalized_communications(sink)


def _codex_semantics(records: list[dict], tmp_path: Path) -> list[dict[str, str]]:
    rollout_path = tmp_path / "codex.jsonl"
    _write_jsonl(rollout_path, records)
    sink = _FakeActionSink()
    CODEX_INGESTOR._parse_codex_rollout(
        rollout_path,
        SENDER_ROLE,
        set(),
        ALL_AGENT_ROLES,
        sink,
        expected_team_name=EXPECTED_TEAM,
    )
    return _normalized_communications(sink)


def _openclaw_semantics(records: list[dict], tmp_path: Path) -> list[dict[str, str]]:
    session_path = tmp_path / "openclaw.jsonl"
    _write_jsonl(session_path, records)
    sink = _FakeActionSink()
    OPENCLAW_INGESTOR._parse_openclaw_session(
        session_path,
        SENDER_ROLE,
        set(),
        ALL_AGENT_ROLES,
        sink,
        expected_team_name=EXPECTED_TEAM,
    )
    return _normalized_communications(sink)


def _claude_send_records(*, content: str, team_name: str, result_text: str, is_error: bool = False) -> list[dict]:
    return [
        {
            "type": "assistant",
            "timestamp": "2026-04-22T10:00:01Z",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "mail-1",
                        "name": "clawteam__mailbox_send",
                        "input": {
                            "team_name": team_name,
                            "to": "concierge_lead",
                            "content": content,
                        },
                    }
                ]
            },
        },
        {
            "type": "user",
            "timestamp": "2026-04-22T10:00:02Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "mail-1",
                        "content": result_text,
                        "is_error": is_error,
                    }
                ]
            },
        },
    ]


def _codex_send_records(*, content: str, team_name: str, result_text: str) -> list[dict]:
    return [
        {
            "type": "response_item",
            "timestamp": "2026-04-22T10:00:01Z",
            "payload": {
                "type": "function_call",
                "call_id": "mail-1",
                "name": "clawteam__mailbox_send",
                "arguments": json.dumps(
                    {
                        "team_name": team_name,
                        "to": "concierge_lead",
                        "content": content,
                    }
                ),
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-04-22T10:00:02Z",
            "payload": {
                "type": "function_call_output",
                "call_id": "mail-1",
                "output": result_text,
            },
        },
    ]


def _openclaw_send_records(*, content: str, team_name: str, result_text: str, status: str, is_error: bool = False) -> list[dict]:
    return [
        {
            "type": "message",
            "timestamp": "2026-04-22T10:00:01Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "mail-1",
                        "name": "clawteam__mailbox_send",
                        "arguments": {
                            "team_name": team_name,
                            "to": "concierge_lead",
                            "content": content,
                        },
                    }
                ],
            },
        },
        {
            "type": "message",
            "timestamp": "2026-04-22T10:00:02Z",
            "message": {
                "role": "toolResult",
                "toolCallId": "mail-1",
                "toolName": "clawteam__mailbox_send",
                "content": [{"type": "text", "text": result_text}],
                "details": {
                    "status": status,
                    "structuredContent": {
                        "result": {"timestamp": "2026-04-22T10:00:02+00:00"}
                    },
                },
                "isError": is_error,
            },
        },
    ]


def _claude_broadcast_records(*, content: str, team_name: str, exclude: list[str]) -> list[dict]:
    return [
        {
            "type": "assistant",
            "timestamp": "2026-04-22T10:01:01Z",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "broadcast-1",
                        "name": "clawteam__mailbox_broadcast",
                        "input": {
                            "team_name": team_name,
                            "content": content,
                            "exclude": exclude,
                        },
                    }
                ]
            },
        },
        {
            "type": "user",
            "timestamp": "2026-04-22T10:01:03Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "broadcast-1",
                        "content": '{"type":"broadcast"}',
                    }
                ]
            },
        },
    ]


def _codex_broadcast_records(*, content: str, team_name: str, exclude: list[str]) -> list[dict]:
    return [
        {
            "type": "response_item",
            "timestamp": "2026-04-22T10:01:01Z",
            "payload": {
                "type": "function_call",
                "call_id": "broadcast-1",
                "name": "clawteam__mailbox_broadcast",
                "arguments": json.dumps(
                    {
                        "team_name": team_name,
                        "content": content,
                        "exclude": exclude,
                    }
                ),
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-04-22T10:01:03Z",
            "payload": {
                "type": "function_call_output",
                "call_id": "broadcast-1",
                "output": '{"type":"broadcast"}',
            },
        },
    ]


def _openclaw_broadcast_records(*, content: str, team_name: str, exclude: list[str]) -> list[dict]:
    return [
        {
            "type": "message",
            "timestamp": "2026-04-22T10:01:01Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "broadcast-1",
                        "name": "clawteam__mailbox_broadcast",
                        "arguments": {
                            "team_name": team_name,
                            "content": content,
                            "exclude": exclude,
                        },
                    }
                ],
            },
        },
        {
            "type": "message",
            "timestamp": "2026-04-22T10:01:03Z",
            "message": {
                "role": "toolResult",
                "toolCallId": "broadcast-1",
                "toolName": "clawteam__mailbox_broadcast",
                "content": [{"type": "text", "text": '{"type":"broadcast"}'}],
                "details": {
                    "status": "ok",
                    "structuredContent": {
                        "result": {"timestamp": "2026-04-22T10:01:03+00:00"}
                    },
                },
                "isError": False,
            },
        },
    ]


def test_successful_mailbox_send_is_normalized_consistently_across_harnesses(tmp_path: Path) -> None:
    expected = [
        {
            "agent_role": SENDER_ROLE,
            "target_role": "concierge_lead",
            "content": "budget fits",
            "timestamp": "2026-04-22T10:00:02+00:00",
        }
    ]

    claude = _claude_semantics(
        _claude_send_records(
            content="budget fits",
            team_name=EXPECTED_TEAM,
            result_text='{"type":"message"}',
        ),
        tmp_path,
    )
    codex = _codex_semantics(
        _codex_send_records(
            content="budget fits",
            team_name=EXPECTED_TEAM,
            result_text='{"type":"message"}',
        ),
        tmp_path,
    )
    openclaw = _openclaw_semantics(
        _openclaw_send_records(
            content="budget fits",
            team_name=EXPECTED_TEAM,
            result_text='{"type":"message"}',
            status="ok",
        ),
        tmp_path,
    )

    _assert_semantics_match(expected, claude, codex, openclaw)


def test_failed_mailbox_send_is_filtered_consistently_across_harnesses(tmp_path: Path) -> None:
    expected: list[dict[str, str]] = []

    claude = _claude_semantics(
        _claude_send_records(
            content="should not land",
            team_name=EXPECTED_TEAM,
            result_text="Error executing tool mailbox_send: bad request",
            is_error=True,
        ),
        tmp_path,
    )
    codex = _codex_semantics(
        _codex_send_records(
            content="should not land",
            team_name=EXPECTED_TEAM,
            result_text="Error executing tool mailbox_send: bad request",
        ),
        tmp_path,
    )
    openclaw = _openclaw_semantics(
        _openclaw_send_records(
            content="should not land",
            team_name=EXPECTED_TEAM,
            result_text="Error executing tool mailbox_send: bad request",
            status="error",
        ),
        tmp_path,
    )

    _assert_semantics_match(expected, claude, codex, openclaw)


def test_wrong_team_mailbox_send_is_filtered_consistently_across_harnesses(tmp_path: Path) -> None:
    expected: list[dict[str, str]] = []

    claude = _claude_semantics(
        _claude_send_records(
            content="wrong team",
            team_name="masp-other",
            result_text='{"type":"message"}',
        ),
        tmp_path,
    )
    codex = _codex_semantics(
        _codex_send_records(
            content="wrong team",
            team_name="masp-other",
            result_text='{"type":"message"}',
        ),
        tmp_path,
    )
    openclaw = _openclaw_semantics(
        _openclaw_send_records(
            content="wrong team",
            team_name="masp-other",
            result_text='{"type":"message"}',
            status="ok",
        ),
        tmp_path,
    )

    _assert_semantics_match(expected, claude, codex, openclaw)


def test_mailbox_broadcast_expansion_is_normalized_consistently_across_harnesses(tmp_path: Path) -> None:
    expected = [
        {
            "agent_role": SENDER_ROLE,
            "target_role": "concierge_lead",
            "content": "team update",
            "timestamp": "2026-04-22T10:01:03+00:00",
        },
        {
            "agent_role": SENDER_ROLE,
            "target_role": "vendor_liaison",
            "content": "team update",
            "timestamp": "2026-04-22T10:01:03+00:00",
        },
    ]

    claude = _claude_semantics(
        _claude_broadcast_records(
            content="team update",
            team_name=EXPECTED_TEAM,
            exclude=["booking_specialist"],
        ),
        tmp_path,
    )
    codex = _codex_semantics(
        _codex_broadcast_records(
            content="team update",
            team_name=EXPECTED_TEAM,
            exclude=["booking_specialist"],
        ),
        tmp_path,
    )
    openclaw = _openclaw_semantics(
        _openclaw_broadcast_records(
            content="team update",
            team_name=EXPECTED_TEAM,
            exclude=["booking_specialist"],
        ),
        tmp_path,
    )

    _assert_semantics_match(expected, claude, codex, openclaw)
