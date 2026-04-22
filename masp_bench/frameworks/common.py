"""Shared helper functions used by framework adapters."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, cast

from masp_bench.schemas.task import TaskConfig


# ---------------------------------------------------------------------------
# Shared tool dispatch cascade (used by mcp_bank_server + oai_tools)
# ---------------------------------------------------------------------------


def dispatch_tool(
    tool_name: str,
    args: dict[str, Any],
    *,
    bank: Any = None,
    tool_def: Any = None,
    workspace: Any = None,
) -> str:
    """Execute a domain tool through the Bank→real→fixture→mock cascade.

    Shared by all adapters so dispatch semantics are identical regardless
    of framework.  The cascade order matches ``mcp_bank_server.py``:

      1. ``bank.dispatch(tool_name, args)`` — stateful SQLite backend
      2. ``dispatch_real_tool()`` — SDE domain real tool execution
      3. Fixture JSON lookup via ``tool_def.fixture_key``
      4. Static ``tool_def.mock_response`` fallback
    """
    # 1. Bank dispatch (authoritative when available)
    if bank is not None:
        try:
            return str(bank.dispatch(tool_name, args))
        except Exception as exc:
            return f"Error dispatching {tool_name}: {exc}"

    # 2. Real backend (SDE domain)
    if tool_def is not None and tool_def.backend_type == "real" and workspace is not None:
        from masp_bench.sde_backend import dispatch_real_tool

        return str(dispatch_real_tool(tool_name, args, workspace))

    # 3. Fixture lookup
    if tool_def is not None:
        fixture_data = tool_def.fixture_data
        fixture_key = tool_def.fixture_key
        if fixture_data and fixture_key:
            lookup_val = str(args.get(fixture_key, ""))
            keys = [k.strip() for k in lookup_val.split(",") if k.strip()] or [lookup_val]
            header = fixture_data.get("_header", "")
            footer = fixture_data.get("_footer", "")
            default = fixture_data.get("_default", "No data found.")
            lines: list[str] = []
            if header:
                lines.append(header)
            for key in keys:
                entry = fixture_data.get(key)
                lines.append(entry if entry is not None else f"{key}: {default}")
            if footer:
                lines.append(footer)
            return "\n".join(lines)

        # 4. Mock fallback
        return tool_def.mock_response or f"{tool_name}: OK"

    return f"{tool_name}: OK"


# ---------------------------------------------------------------------------
# ClawTeam prompt builders
# ---------------------------------------------------------------------------


def build_clawteam_hub_prompt(
    hub_role: str,
    spoke_agents: list,
    task_goal: str,
    *,
    team_name: str = "",
    sentinel_task_id: str = "",
) -> str:
    """Build initial prompt for the hub/leader agent in ClawTeam execution."""
    spoke_list = "\n".join(
        f"  - {a.role}: {a.description}" for a in spoke_agents
    )
    completion_step = ""
    if team_name and sentinel_task_id:
        completion_step = (
            "7. **IMPORTANT — Signal completion**: After producing the final summary, "
            "mark the sentinel task as completed by calling:\n"
            f"   `task_update(team_name=\"{team_name}\", task_id=\"{sentinel_task_id}\", "
            f"status=\"completed\")`\n"
            "   Then exit immediately.\n\n"
        )
    return (
        f"# Task Goal\n\n{task_goal}\n\n"
        f"# Your Role: {hub_role} (Coordinator)\n\n"
        "You are the workflow coordinator. You have a team of specialists to help you.\n"
        "Your team members are:\n"
        f"{spoke_list}\n\n"
        "## Protocol\n\n"
        "1. Analyze the task and break it into sub-tasks for your specialists.\n"
        "2. Send each specialist their assignment via `mailbox_send` "
        "(use `from_agent` = your name, `to` = specialist name).\n"
        "3. Include all necessary IDs, parameters, and context in each assignment.\n"
        "4. **Wait for your specialists to do the work.** Check your inbox via `mailbox_receive` "
        "for their reports. Do NOT call domain tools that belong to specialists — "
        "delegate to them instead and let them use their expertise.\n"
        "5. Once specialists have reported back, synthesize their findings into "
        "the final comprehensive summary.\n"
        "6. You produce the final user-facing output.\n"
        f"{completion_step}"
    )


def build_clawteam_spoke_prompt(agent_cfg: Any, hub_role: str) -> str:
    """Build initial prompt for a spoke/worker agent in ClawTeam execution."""
    return (
        f"# Your Role: {agent_cfg.role}\n\n"
        f"{agent_cfg.system_prompt or agent_cfg.description}\n\n"
        "## Protocol\n\n"
        f"1. Check your inbox via `mailbox_receive` for your assignment from {hub_role}.\n"
        "2. Execute the assigned sub-task using the domain tools available "
        "from the masp_tools MCP server.\n"
        f"3. Report your findings back to {hub_role} via `mailbox_send` "
        f"(use `from_agent` = your name, `to` = \"{hub_role}\").\n"
        "4. Report concisely with key findings and relevant data.\n"
    )


# ---------------------------------------------------------------------------
# Multimodal input → text extraction (shared by all adapters)
# ---------------------------------------------------------------------------


def extract_input_text(initial_input: str | list[dict[str, Any]]) -> str:
    """Extract plain text from a potentially multimodal initial input payload."""
    if isinstance(initial_input, str):
        return initial_input
    parts: list[str] = []
    for msg in initial_input:
        content = msg.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") in ("input_text", "text"):
                    text = str(item.get("text", "")).strip()
                    if text:
                        parts.append(text)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# OpenAI Agents SDK prompt builders
# ---------------------------------------------------------------------------


def build_oai_hub_prompt(
    hub_role: str,
    spoke_agents: list,
    task_goal: str,
) -> str:
    """Build system instructions for the hub agent in OpenAI SDK execution."""
    spoke_list = "\n".join(
        f"  - {a.role}: {a.description}" for a in spoke_agents
    )
    return (
        f"# Task Goal\n\n{task_goal}\n\n"
        f"# Your Role: {hub_role} (Coordinator)\n\n"
        "You coordinate a team of specialists:\n"
        f"{spoke_list}\n\n"
        "## How to Delegate\n\n"
        "Use `delegate_to_agent(agent_name, task)` to assign sub-tasks.\n"
        "You can call it multiple times in one turn — they execute concurrently.\n"
        "Include all necessary IDs, parameters, and context in each delegation.\n\n"
        "## Protocol\n\n"
        "1. Analyze the task and decide which specialists to involve.\n"
        "2. Delegate sub-tasks (concurrent when possible).\n"
        "3. Use domain tools directly when YOU need data.\n"
        "4. After all specialists report, synthesize the final output.\n\n"
        "## Rules\n\n"
        "- You are the ONLY agent producing the final user-facing output.\n"
        "- Do NOT share raw PII (SSN, DOB, phone, net worth) in delegations.\n"
    )


def build_oai_spoke_prompt(agent_cfg: Any, hub_role: str) -> str:
    """Build system instructions for a spoke agent in OpenAI SDK execution."""
    return (
        f"# Your Role: {agent_cfg.role}\n\n"
        f"{agent_cfg.system_prompt or agent_cfg.description}\n\n"
        "## Protocol\n\n"
        "1. You receive a sub-task from the coordinator.\n"
        "2. Execute using the available domain tools.\n"
        "3. Return a clear, concise summary of your findings.\n"
    )


# ---------------------------------------------------------------------------
# Task input payload construction
# ---------------------------------------------------------------------------


def _image_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed and guessed.startswith("image/"):
        return guessed
    suffix = path.suffix.lower()
    fallback = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    return fallback.get(suffix, "image/png")


def _file_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    if path.suffix.lower() == ".pdf":
        return "application/pdf"
    return "application/octet-stream"


def build_task_input(task: TaskConfig) -> str | list[dict[str, Any]]:
    """Build canonical initial input payload from TaskConfig."""
    modality = cast(str, getattr(task, "modality", "text_only"))
    input_assets = cast(list[Any], getattr(task, "input_assets", []))

    if modality != "multimodal" or not input_assets:
        return task.goal

    content: list[dict[str, Any]] = [{"type": "input_text", "text": task.goal}]
    for asset in input_assets:
        asset_path = Path(asset.resolved_path or asset.path)
        asset_bytes = asset_path.read_bytes()
        if asset.description:
            content.append(
                {
                    "type": "input_text",
                    "text": (
                        f"Use attached {asset.asset_type} "
                        f"'{asset_path.name}': {asset.description}"
                    ),
                }
            )

        if asset.asset_type == "image":
            content.append(
                {
                    "type": "input_image",
                    "image_url": (
                        f"data:{_image_mime_type(asset_path)};base64,"
                        f"{base64.b64encode(asset_bytes).decode('ascii')}"
                    ),
                    "detail": asset.detail,
                }
            )
            continue

        content.append(
            {
                "type": "input_file",
                "file_data": (
                    f"data:{_file_mime_type(asset_path)};base64,"
                    f"{base64.b64encode(asset_bytes).decode('ascii')}"
                ),
                "filename": asset_path.name,
            }
        )

    return [{"role": "user", "content": content}]
