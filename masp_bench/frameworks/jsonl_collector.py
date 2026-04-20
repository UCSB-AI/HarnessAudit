"""Collect and parse Claude Code JSONL session logs into MASP_Bench trace format.

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

log = logging.getLogger(__name__)

MCP_SERVER_NAME = "masp_tools"


# ---------------------------------------------------------------------------
# Path encoding
# ---------------------------------------------------------------------------

def _encode_project_path(worktree_path: Path) -> str:
    """Encode a filesystem path to Claude Code's project directory name.

    Verified against real data::

        /home/chengzhi.ucsb/.clawteam/workspaces/demo/backend-dev
        → -home-chengzhi-ucsb--clawteam-workspaces-demo-backend-dev
    """
    return "-" + str(worktree_path).lstrip("/").replace("/", "-").replace(".", "-")


# ---------------------------------------------------------------------------
# JSONL discovery
# ---------------------------------------------------------------------------

def find_agent_jsonl(worktree_path: Path) -> list[Path]:
    """Find ALL JSONL files for an agent's most recent Claude Code session.

    Returns a list where the first element is the main session JSONL and any
    subsequent elements are sub-agent JSONLs.  Returns empty list if nothing
    is found.
    """
    encoded = _encode_project_path(worktree_path)
    project_dir = Path.home() / ".claude" / "projects" / encoded
    if not project_dir.exists():
        log.warning("No Claude Code project dir for %s (expected %s)", worktree_path, project_dir)
        return []

    main_files = sorted(project_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime)
    if not main_files:
        log.warning("No JSONL files in %s", project_dir)
        return []

    latest = main_files[-1]
    result = [latest]

    # Collect sub-agent JSONLs
    session_id = latest.stem
    subagent_dir = project_dir / session_id / "subagents"
    if subagent_dir.exists():
        result.extend(sorted(subagent_dir.glob("*.jsonl")))

    return result


# ---------------------------------------------------------------------------
# MCP tool-name helpers
# ---------------------------------------------------------------------------

def _strip_mcp_prefix(raw_name: str, server_name: str) -> str:
    """Strip MCP namespace prefix.

    ``mcp__masp_tools__execute_trade`` → ``execute_trade``
    """
    for prefix in (
        f"mcp__{server_name}__",
        f"mcp__{server_name.replace('-', '_')}__",
    ):
        if raw_name.startswith(prefix):
            return raw_name[len(prefix):]
    return raw_name


def _is_mailbox_send(name: str) -> bool:
    return name == "mailbox_send" or name.endswith("__mailbox_send")


def _is_mailbox_broadcast(name: str) -> bool:
    return name == "mailbox_broadcast" or name.endswith("__mailbox_broadcast")


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------

def parse_jsonl(
    jsonl_path: Path,
    agent_role: str,
    catalog_tool_names: set[str],
    all_agent_roles: list[str],
    action_sink: Any,
    *,
    mcp_server_name: str = MCP_SERVER_NAME,
    is_subagent: bool = False,
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
        MCP server name used for prefix stripping (default ``masp_tools``).
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
    _sub_flag: dict[str, Any] = {"is_subagent": True} if is_subagent else {}

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
                            }

                        elif _is_mailbox_send(raw_name):
                            # Point-to-point message → COMMUNICATION
                            action_sink.emit_communication(
                                agent_id=agent_role,
                                agent_role=agent_role,
                                target_agent=tool_args.get("to", ""),
                                target_role=tool_args.get("to", ""),
                                content=str(tool_args.get("content", "")),
                                raw_event={
                                    "framework": "clawteam",
                                    "tool": raw_name,
                                    "capture_mode": "jsonl_mailbox_send",
                                    **_sub_flag,
                                },
                                kind="inbox_message",
                            )

                        elif _is_mailbox_broadcast(raw_name):
                            # Broadcast → expand to per-recipient COMMUNICATION
                            # so V-IC rules can detect spoke→spoke paths.
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
                                            "tool": raw_name,
                                            "capture_mode": "jsonl_mailbox_broadcast",
                                            "expanded_from": "broadcast",
                                            **_sub_flag,
                                        },
                                        kind="broadcast",
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
                            tool_result=result_text[:500],
                            raw_event={
                                "framework": "clawteam",
                                "raw_tool_name": tc["raw_name"],
                                "capture_mode": "jsonl_tool_result",
                                **_sub_flag,
                            },
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
                **_sub_flag,
            },
        )

    return last_assistant_text


def collect_all_agent_traces(
    worktree_map: dict[str, Path],
    catalog_tool_names: set[str],
    all_agent_roles: list[str],
    action_sink: Any,
    hub_role: str,
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

    Returns
    -------
    str
        Hub agent's final output text.
    """
    final_text = ""

    for agent_role, wt_path in worktree_map.items():
        jsonl_files = find_agent_jsonl(wt_path)
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
            )
            # Final output = last text from hub's main JSONL (first file only)
            if agent_role == hub_role and idx == 0:
                final_text = agent_output

    return final_text
