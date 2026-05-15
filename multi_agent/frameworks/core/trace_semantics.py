"""Shared trace-ingestion semantics across harness-specific artifact parsers."""

from __future__ import annotations

from typing import Any


def normalize_mcp_tool_name(raw_name: str, server_name: str) -> str:
    """Strip harness-specific MCP namespace prefixes from tool names."""

    server_names = (server_name, "masp_tools")
    for name in dict.fromkeys(server_names):
        for prefix in (
            f"mcp__{name}__",
            f"mcp__{name.replace('-', '_')}__",
            f"{name}__",
            f"{name.replace('-', '_')}__",
        ):
            if raw_name.startswith(prefix):
                return raw_name[len(prefix):]
    return raw_name


def is_mailbox_send(name: str) -> bool:
    return name == "mailbox_send" or name.endswith("__mailbox_send")


def is_mailbox_broadcast(name: str) -> bool:
    return name == "mailbox_broadcast" or name.endswith("__mailbox_broadcast")


def mailbox_team_name(tool_args: dict[str, Any]) -> str:
    return str(tool_args.get("team_name") or tool_args.get("teamName") or "")


def mailbox_call_succeeded(
    result_text: str,
    *,
    is_error: bool = False,
    status: str | None = None,
) -> bool:
    if is_error:
        return False
    if status == "error":
        return False
    return not result_text.lstrip().startswith("Error executing tool ")


def mailbox_recipients(
    *,
    kind: str,
    sender_role: str,
    all_agent_roles: list[str],
    tool_args: dict[str, Any],
) -> list[str]:
    if kind == "send":
        recipient = str(tool_args.get("to", ""))
        return [recipient] if recipient else []

    exclude = set(tool_args.get("exclude", []) or [])
    exclude.add(sender_role)
    return [role for role in all_agent_roles if role not in exclude]
