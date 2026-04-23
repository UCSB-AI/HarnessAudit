from __future__ import annotations

import json
from pathlib import Path

from masp_bench.frameworks.openclaw_local_ingestor import (
    ingest_openclaw_local_session,
)


class _FakeActionSink:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []

    def emit_tool_call(self, **kwargs) -> None:
        self.tool_calls.append(kwargs)


def _write_session(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def test_ingest_openclaw_local_session_extracts_tool_calls_and_final_text(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "session.jsonl"
    _write_session(
        session_path,
        [
            {
                "type": "message",
                "timestamp": "2026-04-23T18:01:00Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "id": "call-1",
                            "name": "masp_tools__lookup_account",
                            "arguments": {"account_id": "acct-123"},
                        },
                        {"type": "text", "text": "Final recommendation: hold."},
                    ],
                },
            },
            {
                "type": "message",
                "timestamp": "2026-04-23T18:01:01Z",
                "message": {
                    "role": "toolResult",
                    "toolCallId": "call-1",
                    "content": [{"type": "text", "text": '{"balance": 10}'}],
                },
            },
        ],
    )

    sink = _FakeActionSink()

    result = ingest_openclaw_local_session(
        session_path,
        created_after=0.0,
        agent_role="investment_analyst",
        catalog_tool_names={"lookup_account"},
        action_sink=sink,
    )

    assert result.error is None
    assert result.final_output == "Final recommendation: hold."
    assert len(sink.tool_calls) == 1
    assert sink.tool_calls[0]["tool_name"] == "lookup_account"
    assert sink.tool_calls[0]["tool_args"] == {"account_id": "acct-123"}


def test_ingest_openclaw_local_session_errors_when_missing(tmp_path: Path) -> None:
    sink = _FakeActionSink()

    result = ingest_openclaw_local_session(
        tmp_path / "missing.jsonl",
        created_after=0.0,
        agent_role="investment_analyst",
        catalog_tool_names={"lookup_account"},
        action_sink=sink,
    )

    assert result.final_output == ""
    assert result.error is not None
    assert "not found" in result.error


def test_ingest_openclaw_local_session_errors_on_empty_transcript(tmp_path: Path) -> None:
    session_path = tmp_path / "empty.jsonl"
    _write_session(session_path, [])
    sink = _FakeActionSink()

    result = ingest_openclaw_local_session(
        session_path,
        created_after=0.0,
        agent_role="investment_analyst",
        catalog_tool_names={"lookup_account"},
        action_sink=sink,
    )

    assert result.final_output == ""
    assert result.error is not None
    assert "no assistant output or tool activity" in result.error


def test_ingest_openclaw_local_session_errors_when_session_is_stale(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "stale.jsonl"
    _write_session(
        session_path,
        [
            {
                "type": "message",
                "timestamp": "2026-04-23T18:01:00Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "This should be ignored."}],
                },
            }
        ],
    )
    sink = _FakeActionSink()

    result = ingest_openclaw_local_session(
        session_path,
        created_after=session_path.stat().st_mtime + 1.0,
        agent_role="investment_analyst",
        catalog_tool_names={"lookup_account"},
        action_sink=sink,
    )

    assert result.final_output == ""
    assert result.error is not None
    assert "older than this run" in result.error
