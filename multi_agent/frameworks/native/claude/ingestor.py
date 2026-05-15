"""Collect and parse Claude Code JSONL session logs into HarnessAudit trace format.

Claude Code records every tool call (built-in + MCP) in per-session JSONL files
at ``~/.claude/projects/{encoded-cwd}/{session-id}.jsonl``.  Sub-agents spawned
via the ``Agent`` tool get their own JSONL under
``{session-id}/subagents/agent-*.jsonl``.

This module:
1. Discovers JSONL files for each agent by worktree path.
2. Parses ``tool_use`` / ``tool_result`` pairs.
3. Emits ``ObservableAction`` entries via the shared ``ActionSink``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ...core.trace_semantics import (
    is_mailbox_broadcast as _is_mailbox_broadcast,
)
from ...core.trace_semantics import (
    is_mailbox_send as _is_mailbox_send,
)
from ...core.trace_semantics import (
    mailbox_call_succeeded,
    mailbox_recipients,
    mailbox_team_name,
)
from ...core.trace_semantics import (
    normalize_mcp_tool_name as _strip_mcp_prefix,
)
from ...core.sde_policy import is_sde_native_tool, native_bypass_tool_name

log = logging.getLogger(__name__)

MCP_SERVER_NAME = "multi_agent_tools"


# ---- Path encoding ----

def _encode_project_path(worktree_path: Path) -> str:
    """Encode a filesystem path to Claude Code's project directory name.

    Claude Code replaces ``/``, ``.``, and ``_`` with ``-``::

        /workspace/team/concierge_lead
        → -workspace-team-concierge-lead
    """
    return "-" + (
        str(worktree_path)
        .lstrip("/")
        .replace("/", "-")
        .replace(".", "-")
        .replace("_", "-")
    )


# ---- JSONL discovery ----

def find_agent_jsonl(
    worktree_path: Path,
    *,
    created_after: float = 0.0,
    claude_config_dir: Path | None = None,
) -> list[Path]:
    """Find JSONL files for an agent's Claude Code session created after a timestamp.

    Parameters
    ----------
    worktree_path : Path
        The agent's working directory.
    created_after : float
        Unix timestamp.  Only JSONL files with mtime strictly after this value
        are considered.  This prevents picking up stale sessions from previous
        runs that used the same worktree path.  Pass 0.0 to disable filtering.

    Returns a list where the first element is the main session JSONL and any
    subsequent elements are sub-agent JSONLs.  Returns empty list if nothing
    is found.
    """
    encoded = _encode_project_path(worktree_path)
    config_dir = claude_config_dir or Path.home() / ".claude"
    project_dir = config_dir / "projects" / encoded
    if not project_dir.exists():
        log.warning("No Claude Code project dir for %s (expected %s)", worktree_path, project_dir)
        return []

    candidates = [
        f for f in project_dir.glob("*.jsonl")
        if created_after == 0.0 or f.stat().st_mtime > created_after
    ]
    if not candidates:
        log.warning("No JSONL files (after ts=%.0f) in %s", created_after, project_dir)
        return []

    # Pick the most recent qualifying file.
    latest = max(candidates, key=lambda f: f.stat().st_mtime)
    result = [latest]

    # Collect sub-agent JSONLs
    session_id = latest.stem
    subagent_dir = project_dir / session_id / "subagents"
    if subagent_dir.exists():
        result.extend(sorted(subagent_dir.glob("*.jsonl")))

    return result


# ---- JSONL parsing ----

def parse_jsonl(
    jsonl_path: Path,
    agent_role: str,
    catalog_tool_names: set[str],
    all_agent_roles: list[str],
    action_sink: Any,
    *,
    mcp_server_name: str = MCP_SERVER_NAME,
    is_subagent: bool = False,
    expected_team_name: str | None = None,
    sde_mcp_only: bool = False,
) -> str:
    """Parse one JSONL file and emit ``ObservableAction`` entries.

    Parameters
    ----------
    jsonl_path : Path
        Path to the JSONL file.
    agent_role : str
        Role name of the agent that produced this JSONL.
    catalog_tool_names : set[str]
        Names of domain tools from the ToolCatalog.
    all_agent_roles : list[str]
        All agent roles in the task (needed to expand broadcast).
    action_sink : ActionSink
        Shared sink to emit actions into.
    mcp_server_name : str
        MCP server name used for prefix stripping (default ``multi_agent_tools``).
    is_subagent : bool
        If True, marks emitted actions with ``is_subagent: True`` in raw_event.

    Returns
    -------
    str
        The last assistant text message (used as final output for hub).
    """
    last_assistant_text = ""
    # tool_use_id → {name, args, raw_name}
    pending_tool_calls: dict[str, dict[str, Any]] = {}
    pending_mailbox_calls: dict[str, dict[str, Any]] = {}
    _sub_flag: dict[str, Any] = {"is_subagent": True} if is_subagent else {}

    def _event_timestamp(event: dict[str, Any]) -> str | None:
        ts = event.get("timestamp")
        return str(ts) if ts else None

    with open(jsonl_path) as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg = event.get("message", {})
            content = msg.get("content")
            if not isinstance(content, list):
                content = []

            event_type = event.get("type", "")

            # ---- assistant turn: tool_use + text ----
            if event_type == "assistant":
                for item in content:
                    if not isinstance(item, dict):
                        continue

                    if item.get("type") == "tool_use":
                        raw_name = item.get("name", "")
                        tool_args = item.get("input", {})
                        tool_use_id = item.get("id", "")

                        tool_name = _strip_mcp_prefix(raw_name, mcp_server_name)

                        if tool_name in catalog_tool_names:
                            # Domain tool → queue until we see the result
                            pending_tool_calls[tool_use_id] = {
                                "name": tool_name,
                                "args": tool_args,
                                "raw_name": raw_name,
                                "timestamp": _event_timestamp(event),
                                "line_no": line_no,
                            }

                        elif _is_mailbox_send(raw_name):
                            pending_mailbox_calls[tool_use_id] = {
                                "kind": "send",
                                "args": tool_args,
                                "raw_name": raw_name,
                                "timestamp": _event_timestamp(event),
                                "line_no": line_no,
                            }

                        elif _is_mailbox_broadcast(raw_name):
                            pending_mailbox_calls[tool_use_id] = {
                                "kind": "broadcast",
                                "args": tool_args,
                                "raw_name": raw_name,
                                "timestamp": _event_timestamp(event),
                                "line_no": line_no,
                            }
                        elif sde_mcp_only and is_sde_native_tool("claude", raw_name):
                            action_sink.emit_tool_call(
                                agent_id=agent_role,
                                agent_role=agent_role,
                                tool_name=native_bypass_tool_name(raw_name),
                                tool_args=tool_args if isinstance(tool_args, dict) else {},
                                tool_result="(SDE native harness tool bypass attempt)",
                                raw_event={
                                    "framework": "clawteam",
                                    "harness": "claude",
                                    "raw_tool_name": raw_name,
                                    "capture_mode": "jsonl_sde_native_bypass",
                                    "sde_native_bypass_detected": True,
                                    "source_file": str(jsonl_path),
                                    "source_line": line_no,
                                    **_sub_flag,
                                },
                                timestamp=_event_timestamp(event),
                            )

                    elif item.get("type") == "text":
                        text = item.get("text", "")
                        if text:
                            last_assistant_text = text

            # ---- user turn: tool_result ----
            elif event_type == "user":
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") != "tool_result":
                        continue

                    tool_use_id = item.get("tool_use_id", "")
                    result_content = item.get("content", "")
                    if isinstance(result_content, list):
                        # Claude Code sometimes nests content blocks
                        parts = []
                        for part in result_content:
                            if isinstance(part, dict) and "text" in part:
                                parts.append(part["text"])
                            elif isinstance(part, str):
                                parts.append(part)
                        result_text = "\n".join(parts)
                    else:
                        result_text = str(result_content)

                    if tool_use_id in pending_tool_calls:
                        tc = pending_tool_calls.pop(tool_use_id)
                        action_sink.emit_tool_call(
                            agent_id=agent_role,
                            agent_role=agent_role,
                            tool_name=tc["name"],
                            tool_args=tc["args"],
                            tool_result=result_text,
                            raw_event={
                                "framework": "clawteam",
                                "raw_tool_name": tc["raw_name"],
                                "capture_mode": "jsonl_tool_result",
                                "source_file": str(jsonl_path),
                                "source_line": line_no,
                                **_sub_flag,
                            },
                            timestamp=_event_timestamp(event) or tc.get("timestamp"),
                        )
                    elif tool_use_id in pending_mailbox_calls:
                        mc = pending_mailbox_calls.pop(tool_use_id)
                        if not mailbox_call_succeeded(
                            result_text,
                            is_error=bool(item.get("is_error")),
                        ):
                            continue
                        team_name = mailbox_team_name(mc["args"])
                        if expected_team_name and team_name != expected_team_name:
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
                                "tool": mc["raw_name"],
                                "capture_mode": (
                                    "jsonl_mailbox_send"
                                    if mc["kind"] == "send"
                                    else "jsonl_mailbox_broadcast"
                                ),
                                "team_name": team_name,
                                "source_file": str(jsonl_path),
                                "source_line": line_no,
                                **_sub_flag,
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
                                timestamp=_event_timestamp(event) or mc.get("timestamp"),
                            )

    # Emit any pending tool calls that never got a result (shouldn't happen
    # normally, but guards against truncated sessions).
    for tool_use_id, tc in pending_tool_calls.items():
        action_sink.emit_tool_call(
            agent_id=agent_role,
            agent_role=agent_role,
            tool_name=tc["name"],
            tool_args=tc["args"],
            tool_result="(no result recorded)",
            raw_event={
                "framework": "clawteam",
                "raw_tool_name": tc["raw_name"],
                "capture_mode": "jsonl_orphan_tool_use",
                "source_file": str(jsonl_path),
                "source_line": tc.get("line_no", 0),
                **_sub_flag,
            },
            timestamp=tc.get("timestamp"),
        )

    return last_assistant_text


def collect_all_agent_traces(
    worktree_map: dict[str, Path],
    catalog_tool_names: set[str],
    all_agent_roles: list[str],
    action_sink: Any,
    hub_role: str,
    *,
    run_id: str = "",
    spawn_timestamp: float = 0.0,
    sde_mcp_only: bool = False,
    claude_config_dir: Path | None = None,
) -> str:
    """Collect and parse JSONL traces for all agents in a run.

    Parameters
    ----------
    worktree_map : dict[str, Path]
        Mapping of agent_role → worktree directory path.
    catalog_tool_names : set[str]
        Domain tool names from the ToolCatalog.
    all_agent_roles : list[str]
        All agent role names in the task.
    action_sink : ActionSink
        Shared action sink.
    hub_role : str
        Role name of the hub agent (for final output extraction).
    spawn_timestamp : float
        Unix timestamp just before agents were spawned.  Only JSONL files
        created after this time are considered.

    Returns
    -------
    str
        Hub agent's final output text.
    """
    final_text = ""

    for agent_role, wt_path in worktree_map.items():
        jsonl_files = find_agent_jsonl(
            wt_path,
            created_after=spawn_timestamp,
            claude_config_dir=claude_config_dir,
        )
        if not jsonl_files:
            log.warning("No JSONL found for agent %s at %s", agent_role, wt_path)
            continue

        for idx, jsonl_path in enumerate(jsonl_files):
            log.info("Parsing %s for agent %s%s", jsonl_path.name, agent_role,
                     " (subagent)" if idx > 0 else "")
            agent_output = parse_jsonl(
                jsonl_path,
                agent_role,
                catalog_tool_names,
                all_agent_roles,
                action_sink,
                is_subagent=(idx > 0),
                expected_team_name=f"multi-agent-{run_id}" if run_id else None,
                sde_mcp_only=sde_mcp_only,
            )
            # Final output = last text from hub's main JSONL (first file only)
            if agent_role == hub_role and idx == 0:
                final_text = agent_output

    return final_text
