from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path


def _load_openclaw_ingestor():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "masp_bench" / "frameworks" / "openclaw_ingestor.py"

    masp_pkg = types.ModuleType("masp_bench")
    masp_pkg.__path__ = [str(repo_root / "masp_bench")]
    sys.modules.setdefault("masp_bench", masp_pkg)

    frameworks_pkg = types.ModuleType("masp_bench.frameworks")
    frameworks_pkg.__path__ = [str(repo_root / "masp_bench" / "frameworks")]
    sys.modules.setdefault("masp_bench.frameworks", frameworks_pkg)

    collector_mod = types.ModuleType("masp_bench.frameworks.jsonl_collector")
    collector_mod._is_mailbox_send = (
        lambda name: name == "mailbox_send" or name.endswith("__mailbox_send")
    )
    collector_mod._is_mailbox_broadcast = (
        lambda name: name == "mailbox_broadcast" or name.endswith("__mailbox_broadcast")
    )
    collector_mod._strip_mcp_prefix = (
        lambda name, mcp_server_name: name.split("__", 1)[1]
        if name.startswith(f"{mcp_server_name}__")
        else name
    )
    sys.modules["masp_bench.frameworks.jsonl_collector"] = collector_mod

    harness_mod = types.ModuleType("masp_bench.frameworks.harness_profiles")
    harness_mod.session_name = (
        lambda run_id, agent_name, harness: f"{run_id}-{agent_name}"
        if harness != "claude"
        else agent_name
    )
    sys.modules["masp_bench.frameworks.harness_profiles"] = harness_mod

    spec = importlib.util.spec_from_file_location(
        "masp_bench.frameworks.openclaw_ingestor",
        module_path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


openclaw_ingestor = _load_openclaw_ingestor()


class _FakeActionSink:
    def __init__(self) -> None:
        self.communications: list[dict] = []
        self.tool_calls: list[dict] = []

    def emit_communication(self, **kwargs) -> None:
        self.communications.append(kwargs)

    def emit_tool_call(self, **kwargs) -> None:
        self.tool_calls.append(kwargs)


def _write_session(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def test_parse_openclaw_session_emits_only_successful_matching_team_messages(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "payments.jsonl"
    _write_session(
        session_path,
        [
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "id": "send-ok",
                            "name": "clawteam__mailbox_send",
                            "arguments": {
                                "team_name": "masp-current",
                                "to": "concierge_lead",
                                "content": "current-team reply",
                            },
                        },
                        {
                            "type": "toolCall",
                            "id": "send-wrong-team",
                            "name": "clawteam__mailbox_send",
                            "arguments": {
                                "team_name": "masp-other",
                                "to": "concierge_lead",
                                "content": "wrong-team reply",
                            },
                        },
                        {
                            "type": "toolCall",
                            "id": "send-error",
                            "name": "clawteam__mailbox_send",
                            "arguments": {
                                "team_name": "masp-current",
                                "to": "concierge_lead",
                                "content": "failed reply",
                            },
                        },
                    ],
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "toolResult",
                    "toolCallId": "send-ok",
                    "toolName": "clawteam__mailbox_send",
                    "content": [{"type": "text", "text": '{"type":"message"}'}],
                    "details": {"status": "ok"},
                    "isError": False,
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "toolResult",
                    "toolCallId": "send-wrong-team",
                    "toolName": "clawteam__mailbox_send",
                    "content": [{"type": "text", "text": '{"type":"message"}'}],
                    "details": {"status": "ok"},
                    "isError": False,
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "toolResult",
                    "toolCallId": "send-error",
                    "toolName": "clawteam__mailbox_send",
                    "content": [
                        {
                            "type": "text",
                            "text": "Error executing tool mailbox_send: bad request",
                        }
                    ],
                    "details": {"status": "error"},
                    "isError": False,
                },
            },
        ],
    )

    sink = _FakeActionSink()

    openclaw_ingestor._parse_openclaw_session(
        session_path,
        "payments_and_coverage_specialist",
        set(),
        ["concierge_lead", "payments_and_coverage_specialist"],
        sink,
        expected_team_name="masp-current",
    )

    assert [entry["content"] for entry in sink.communications] == ["current-team reply"]


def test_collect_openclaw_traces_uses_run_scoped_session_name(monkeypatch) -> None:
    requested: list[tuple[str, str, float]] = []

    def fake_find(agent_role: str, session_id: str, *, created_after: float = 0.0):
        requested.append((agent_role, session_id, created_after))
        return []

    monkeypatch.setattr(openclaw_ingestor, "_find_openclaw_sessions", fake_find)

    final_text = openclaw_ingestor.collect_openclaw_traces(
        worktree_map={"concierge_lead": Path("/tmp/concierge")},
        catalog_tool_names=set(),
        all_agent_roles=["concierge_lead"],
        action_sink=_FakeActionSink(),
        hub_role="concierge_lead",
        run_id="ct-openclaw-claude-sonnet-4-a2154a27",
        spawn_timestamp=123.0,
    )

    assert final_text == ""
    assert requested == [
        (
            "concierge_lead",
            "ct-openclaw-claude-sonnet-4-a2154a27-concierge_lead",
            123.0,
        )
    ]
