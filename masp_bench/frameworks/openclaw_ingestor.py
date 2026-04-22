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

from .jsonl_collector import _is_mailbox_broadcast, _is_mailbox_send, _strip_mcp_prefix

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
) -> str:
    """Parse one OpenClaw session JSONL and emit ObservableActions.

    Returns the last assistant text (for hub final output extraction).
    """
    last_assistant_text = ""
    # toolCall id → {name, args, raw_name}
    pending_tool_calls: dict[str, dict[str, Any]] = {}

    with open(session_path) as f:
        for line in f:
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
                            }
                        elif _is_mailbox_send(raw_name):
                            action_sink.emit_communication(
                                agent_id=agent_role,
                                agent_role=agent_role,
                                target_agent=tool_args.get("to", ""),
                                target_role=tool_args.get("to", ""),
                                content=str(tool_args.get("content", "")),
                                raw_event={
                                    "framework": "clawteam",
                                    "harness": "openclaw",
                                    "tool": raw_name,
                                    "capture_mode": "openclaw_session_mailbox_send",
                                },
                            )
                        elif _is_mailbox_broadcast(raw_name):
                            exclude = set(tool_args.get("exclude", []) or [])
                            exclude.add(agent_role)
                            msg_content = str(tool_args.get("content", ""))
                            for recipient in all_agent_roles:
                                if recipient not in exclude:
                                    action_sink.emit_communication(
                                        agent_id=agent_role,
                                        agent_role=agent_role,
                                        target_agent=recipient,
                                        target_role=recipient,
                                        content=msg_content,
                                        raw_event={
                                            "framework": "clawteam",
                                            "harness": "openclaw",
                                            "tool": raw_name,
                                            "capture_mode": "openclaw_session_mailbox_broadcast",
                                            "expanded_from": "broadcast",
                                        },
                                    )

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
                        tool_result=result_text[:500],
                        raw_event={
                            "framework": "clawteam",
                            "harness": "openclaw",
                            "raw_tool_name": tc["raw_name"],
                            "capture_mode": "openclaw_session_tool_result",
                        },
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
            },
        )

    return last_assistant_text


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
        # NativeCliAdapter sets --session-id to agent_name by default
        # (adapters.py:108-109), so session files are just {agent_name}.jsonl
        sid = agent_role
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
            )
            if agent_role == hub_role and idx == 0:
                final_text = agent_output

    return final_text
