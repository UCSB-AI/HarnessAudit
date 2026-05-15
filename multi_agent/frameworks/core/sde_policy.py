"""SDE harness policy helpers.

The software_engineering domain must be completed through multi_agent SDE MCP tools,
not through harness-native filesystem or shell tools.
"""

from __future__ import annotations

import os
from typing import Iterable

SDE_DOMAIN = "software_engineering"
SDE_ALLOW_NATIVE_TOOLS_ENV = "MASP_SDE_ALLOW_NATIVE_TOOLS"
SDE_NATIVE_TOOL_PREFIX = "native::"

SDE_MCP_ALLOWED_TOOLS = (
    "mcp__multi_agent_tools__*",
    "mcp__masp_tools__*",
    "mcp__clawteam__*",
)

SDE_CLAUDE_NATIVE_TOOLS = (
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "Bash",
    "Grep",
    "Glob",
    "WebFetch",
    "WebSearch",
    "Task",
)

SDE_CODEX_NATIVE_TOOLS = (
    "exec_command",
    "write_stdin",
    "shell",
    "shell_command",
    "container.exec",
    "local_shell",
    "apply_patch",
    "list_dir",
    "update_plan",
    "list_mcp_resources",
    "list_mcp_resource_templates",
    "read_mcp_resource",
    "request_permissions",
    "request_user_input",
)

SDE_OPENCLAW_NATIVE_TOOLS = (
    "read",
    "write",
    "edit",
    "apply_patch",
    "grep",
    "find",
    "ls",
    "exec",
    "process",
    "web_search",
    "web_fetch",
    "browser",
)


def sde_mcp_only_enabled(domain: str) -> bool:
    """Return whether SDE native-tool bypass protection should apply."""

    return (
        domain == SDE_DOMAIN
        and os.environ.get(SDE_ALLOW_NATIVE_TOOLS_ENV, "").strip() != "1"
    )


def _normalized_candidates(raw_name: str) -> set[str]:
    stripped = str(raw_name or "").strip()
    if not stripped:
        return set()
    parts = {stripped, stripped.lower()}
    if "__" in stripped:
        tail = stripped.rsplit("__", 1)[-1]
        parts.update({tail, tail.lower()})
    return parts


def _lowered(values: Iterable[str]) -> set[str]:
    return {str(value).lower() for value in values}


def is_sde_native_tool(harness: str, raw_name: str) -> bool:
    """Detect harness-native file/shell tools in SDE-only mode."""

    candidates = _normalized_candidates(raw_name)
    if not candidates:
        return False
    lowered = {candidate.lower() for candidate in candidates}
    if harness == "claude":
        return bool(lowered & _lowered(SDE_CLAUDE_NATIVE_TOOLS))
    if harness == "codex":
        return bool(lowered & _lowered(SDE_CODEX_NATIVE_TOOLS))
    if harness == "openclaw":
        return bool(lowered & _lowered(SDE_OPENCLAW_NATIVE_TOOLS))
    return False


def native_bypass_tool_name(raw_name: str) -> str:
    """Normalize a native tool attempt into a trace-visible synthetic tool."""

    return f"{SDE_NATIVE_TOOL_PREFIX}{str(raw_name or 'unknown').strip() or 'unknown'}"
