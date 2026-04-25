"""OpenClaw trace discovery and parsing for MASP_Bench.

OpenClaw stores session transcripts as JSONL files at::

    ~/.openclaw/agents/main/sessions/{session-id}.jsonl

Each line is a typed event (verified via Phase 0 probe):

- ``type: "session"``       — header (id, version, cwd, timestamp)
- ``type: "message"``       — conversation turn:
    - ``role: "user"``      — user input
    - ``role: "assistant"`` — model response with tool calls and/or text
    - ``role: "toolResult"``— tool execution result
- ``type: "model_change"``  — model selection event
- ``type: "custom"``        — metadata events

Tool calls appear in ``role: "assistant"`` messages as content items:

    {"type": "toolCall", "id": "call_...", "name": "mcp__masp_tools__X", "arguments": {...}}

Tool results appear as separate messages:

    {"type": "message", "message": {"role": "toolResult", "toolCallId": "call_...",
     "toolName": "...", "content": [{"type": "text", "text": "..."}]}}

This is structurally identical to Claude Code's tool_use/tool_result pattern.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .harness_profiles import session_name
from .trace_semantics import (
    is_mailbox_broadcast as _is_mailbox_broadcast,
    is_mailbox_send as _is_mailbox_send,
    mailbox_call_succeeded,
    mailbox_recipients,
    mailbox_team_name,
    normalize_mcp_tool_name as _strip_mcp_prefix,
)

log = logging.getLogger(__name__)

MCP_SERVER_NAME = "masp_tools"

# OpenClaw stores sessions under ~/.openclaw/agents/main/sessions/
_OPENCLAW_SESSIONS_DIR = Path.home() / ".openclaw" / "agents" / "main" / "sessions"


# ---------------------------------------------------------------------------
# Artifact discovery
# ---------------------------------------------------------------------------

def _find_openclaw_sessions(
    agent_role: str,
    session_id: str,
    *,
    created_after: float = 0.0,
) -> list[Path]:
    """Find OpenClaw session JSONL for an agent.

    The session ID is set via ``--session-id`` at spawn time (controlled by
    ``HarnessRuntime.session_name()``).  The JSONL file is at
    ``~/.openclaw/agents/main/sessions/{session-id}.jsonl``.
    """
    session_file = _OPENCLAW_SESSIONS_DIR / f"{session_id}.jsonl"
    if not session_file.exists():
        log.warning("No OpenClaw session file for %s at %s", agent_role, session_file)
        return []

    if created_after > 0.0 and session_file.stat().st_mtime <= created_after:
        log.warning("OpenClaw session %s is older than spawn timestamp", session_file)
        return []

    return [session_file]


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------

def _parse_openclaw_session(
    session_path: Path,
    agent_role: str,
    catalog_tool_names: set[str],
    all_agent_roles: list[str],
    action_sink: Any,
    *,
    mcp_server_name: str = MCP_SERVER_NAME,
    expected_team_name: str | None = None,
) -> str:
    """Parse one OpenClaw session JSONL and emit ObservableActions.

    Returns the last assistant text (for hub final output extraction).
    """
    last_assistant_text = ""
    # toolCall id → {name, args, raw_name}
    pending_tool_calls: dict[str, dict[str, Any]] = {}
    pending_mailbox_calls: dict[str, dict[str, Any]] = {}

    def _record_timestamp(record: dict[str, Any]) -> str | None:
        ts = record.get("timestamp")
        return str(ts) if ts else None

    def _mailbox_result_timestamp(record: dict[str, Any], message: dict[str, Any]) -> str | None:
        details = message.get("details", {})
        if isinstance(details, dict):
            structured = details.get("structuredContent", {})
            if isinstance(structured, dict):
                result = structured.get("result", {})
                if isinstance(result, dict):
                    result_ts = result.get("timestamp")
                    if result_ts:
                        return str(result_ts)
        return _record_timestamp(record)

    with open(session_path) as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            record_type = record.get("type", "")
            if record_type != "message":
                continue

            msg = record.get("message", {})
            role = msg.get("role", "")
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue

            # ---- assistant turn: toolCall + text ----
            if role == "assistant":
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    ctype = item.get("type", "")

                    if ctype == "toolCall":
                        raw_name = item.get("name", "")
                        tool_args = item.get("arguments", {})
                        call_id = item.get("id", "")

                        if isinstance(tool_args, str):
                            try:
                                tool_args = json.loads(tool_args)
                            except json.JSONDecodeError:
                                tool_args = {"_raw": tool_args}

                        tool_name = _strip_mcp_prefix(raw_name, mcp_server_name)

                        if tool_name in catalog_tool_names:
                            pending_tool_calls[call_id] = {
                                "name": tool_name,
                                "args": tool_args,
                                "raw_name": raw_name,
                                "timestamp": _record_timestamp(record),
                                "line_no": line_no,
                            }
                        elif _is_mailbox_send(raw_name):
                            pending_mailbox_calls[call_id] = {
                                "kind": "send",
                                "args": tool_args,
                                "raw_name": raw_name,
                                "timestamp": _record_timestamp(record),
                                "line_no": line_no,
                            }
                        elif _is_mailbox_broadcast(raw_name):
                            pending_mailbox_calls[call_id] = {
                                "kind": "broadcast",
                                "args": tool_args,
                                "raw_name": raw_name,
                                "timestamp": _record_timestamp(record),
                                "line_no": line_no,
                            }

                    elif ctype == "text":
                        text = item.get("text", "")
                        if text:
                            last_assistant_text = text

            # ---- toolResult: paired with pending toolCall ----
            elif role == "toolResult":
                call_id = msg.get("toolCallId", "")
                result_parts = []
                for item in content:
                    if isinstance(item, dict) and "text" in item:
                        result_parts.append(item["text"])
                    elif isinstance(item, str):
                        result_parts.append(item)
                result_text = "\n".join(result_parts)

                if call_id in pending_tool_calls:
                    tc = pending_tool_calls.pop(call_id)
                    action_sink.emit_tool_call(
                        agent_id=agent_role,
                        agent_role=agent_role,
                        tool_name=tc["name"],
                        tool_args=tc["args"],
                        tool_result=result_text,
                        raw_event={
                            "framework": "clawteam",
                            "harness": "openclaw",
                            "raw_tool_name": tc["raw_name"],
                            "capture_mode": "openclaw_session_tool_result",
                            "source_file": str(session_path),
                            "source_line": line_no,
                        },
                        timestamp=_record_timestamp(record) or tc.get("timestamp"),
                    )
                elif call_id in pending_mailbox_calls:
                    mc = pending_mailbox_calls.pop(call_id)
                    details = msg.get("details", {})
                    status = details.get("status") if isinstance(details, dict) else None
                    if not mailbox_call_succeeded(
                        result_text,
                        is_error=bool(msg.get("isError")),
                        status=status,
                    ):
                        continue
                    message_team = mailbox_team_name(mc["args"])
                    if expected_team_name and message_team != expected_team_name:
                        continue

                    msg_content = str(mc["args"].get("content", ""))
                    for recipient in mailbox_recipients(
                        kind=mc["kind"],
                        sender_role=agent_role,
                        all_agent_roles=all_agent_roles,
                        tool_args=mc["args"],
                    ):
                        raw_event = {
                            "framework": "clawteam",
                            "harness": "openclaw",
                            "tool": mc["raw_name"],
                            "capture_mode": (
                                "openclaw_session_mailbox_send"
                                if mc["kind"] == "send"
                                else "openclaw_session_mailbox_broadcast"
                            ),
                            "team_name": message_team,
                            "source_file": str(session_path),
                            "source_line": line_no,
                        }
                        if mc["kind"] == "broadcast":
                            raw_event["expanded_from"] = "broadcast"
                        action_sink.emit_communication(
                            agent_id=agent_role,
                            agent_role=agent_role,
                            target_agent=recipient,
                            target_role=recipient,
                            content=msg_content,
                            raw_event=raw_event,
                            timestamp=(
                                _mailbox_result_timestamp(record, msg)
                                or mc.get("timestamp")
                            ),
                        )

    # Emit orphan tool calls
    for call_id, tc in pending_tool_calls.items():
        action_sink.emit_tool_call(
            agent_id=agent_role,
            agent_role=agent_role,
            tool_name=tc["name"],
            tool_args=tc["args"],
            tool_result="(no result recorded)",
            raw_event={
                "framework": "clawteam",
                "harness": "openclaw",
                "raw_tool_name": tc["raw_name"],
                "capture_mode": "openclaw_session_orphan_tool_use",
                "source_file": str(session_path),
                "source_line": tc.get("line_no", 0),
            },
            timestamp=tc.get("timestamp"),
        )

    return last_assistant_text


def read_openclaw_final_text(session_path: Path) -> tuple[str, dict[str, Any]]:
    """Read only the last assistant text from a session without emitting actions."""
    last_assistant_text = ""
    last_message_role = ""
    last_content_type = ""

    with open(session_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            if record.get("type") != "message":
                continue

            message = record.get("message", {})
            role = message.get("role", "")
            content = message.get("content", [])
            if not isinstance(content, list):
                continue

            last_message_role = role
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type", "")
                if item_type:
                    last_content_type = item_type
                if role == "assistant" and item_type == "text":
                    text = item.get("text", "")
                    if text:
                        last_assistant_text = text

    meta = {
        "session_path": str(session_path),
        "found_final_text": bool(last_assistant_text),
        "last_message_role": last_message_role,
        "last_content_type": last_content_type,
    }
    return last_assistant_text, meta


def read_openclaw_final_output(
    *,
    run_id: str,
    hub_role: str,
    spawn_timestamp: float = 0.0,
) -> str:
    """Read the hub's final text without re-emitting tool actions."""
    sid = session_name(run_id, hub_role, "openclaw")
    session_files = _find_openclaw_sessions(
        hub_role,
        sid,
        created_after=spawn_timestamp,
    )
    if not session_files:
        log.info(
            "No OpenClaw hub session available for late final-text read (role=%s, session=%s)",
            hub_role,
            sid,
        )
        return ""

    session_path = session_files[0]
    final_text, meta = read_openclaw_final_text(session_path)
    log.info(
        "Late OpenClaw final-text read from %s: found=%s last_role=%s last_content_type=%s",
        session_path,
        meta["found_final_text"],
        meta["last_message_role"] or "<none>",
        meta["last_content_type"] or "<none>",
    )
    return final_text


# ---------------------------------------------------------------------------
# Top-level collection entry point
# ---------------------------------------------------------------------------

def collect_openclaw_traces(
    worktree_map: dict[str, Path],
    catalog_tool_names: set[str],
    all_agent_roles: list[str],
    action_sink: Any,
    hub_role: str,
    *,
    run_id: str = "",
    spawn_timestamp: float = 0.0,
) -> str:
    """Collect and parse OpenClaw session traces for all agents in a run.

    Returns the hub agent's final output text.
    """
    final_text = ""

    for agent_role in worktree_map:
        sid = session_name(run_id, agent_role, "openclaw") if run_id else agent_role
        session_files = _find_openclaw_sessions(
            agent_role, sid, created_after=spawn_timestamp,
        )
        if not session_files:
            log.warning("No OpenClaw session found for agent %s (session=%s)", agent_role, sid)
            continue

        for idx, session_path in enumerate(session_files):
            log.info("Parsing OpenClaw session %s for agent %s", session_path.name, agent_role)
            agent_output = _parse_openclaw_session(
                session_path,
                agent_role,
                catalog_tool_names,
                all_agent_roles,
                action_sink,
                expected_team_name=f"masp-{run_id}" if run_id else None,
            )
            if agent_role == hub_role and idx == 0:
                final_text = agent_output
                log.info(
                    "Collected OpenClaw hub session %s: found_final_text=%s",
                    session_path,
                    bool(agent_output),
                )

    return final_text
