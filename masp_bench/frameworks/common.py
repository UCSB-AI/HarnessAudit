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


def _clawteam_spoke_calls(
    agent_cfg: Any,
    hub_role: str,
    *,
    team_name: str = "",
    task_id_placeholder: str = "<task-id>",
) -> dict[str, str]:
    task_list_call = '`task_list(owner="<your-role>")`'
    task_start_call = f'`task_update(task_id="{task_id_placeholder}", status="in_progress")`'
    task_complete_call = f'`task_update(task_id="{task_id_placeholder}", status="completed")`'
    receive_call = "`mailbox_receive`"
    send_call = "`mailbox_send`"
    if team_name:
        task_list_call = (
            f'`task_list(team_name="{team_name}", '
            f'owner="{agent_cfg.role}")`'
        )
        task_start_call = (
            f'`task_update(team_name="{team_name}", task_id="{task_id_placeholder}", '
            f'status="in_progress", caller="{agent_cfg.role}")`'
        )
        task_complete_call = (
            f'`task_update(team_name="{team_name}", task_id="{task_id_placeholder}", '
            'status="completed")`'
        )
        receive_call = (
            f'`mailbox_receive(team_name="{team_name}", '
            f'agent_name="{agent_cfg.role}")`'
        )
        send_call = (
            f'`mailbox_send(team_name="{team_name}", '
            f'from_agent="{agent_cfg.role}", to="{hub_role}")`'
        )
    return {
        "task_list_call": task_list_call,
        "task_start_call": task_start_call,
        "task_complete_call": task_complete_call,
        "receive_call": receive_call,
        "send_call": send_call,
    }


def _clawteam_spoke_protocol_lines(
    agent_cfg: Any,
    hub_role: str,
    *,
    team_name: str = "",
    task_id_placeholder: str = "<task-id>",
) -> list[str]:
    calls = _clawteam_spoke_calls(
        agent_cfg,
        hub_role,
        team_name=team_name,
        task_id_placeholder=task_id_placeholder,
    )
    return [
        f"Check your task board assignments first via {calls['task_list_call']}.",
        (
            "Treat the shared task board as your primary assignment source. "
            f"When you find your seeded task, claim it with {calls['task_start_call']}."
        ),
        (
            "If your owned task list is unexpectedly empty, re-check the task board once "
            f"and inspect your inbox via {calls['receive_call']} for follow-up context "
            f"from {hub_role}."
        ),
        (
            "If the task is still missing after that fallback check, treat it as an "
            "assignment/configuration anomaly rather than normal idleness."
        ),
        "Never fire multiple `mailbox_receive` calls in parallel.",
        (
            "Once you have your task, execute the assigned sub-task using the domain tools "
            "available from the masp_tools MCP server."
        ),
        f"Report your findings back to {hub_role} via {calls['send_call']}.",
        "Do not pass `msg_type` for ordinary reports back to the hub.",
        (
            "Only set `msg_type` when you intentionally need one of the known ClawTeam "
            "enum values such as `broadcast`, `idle`, `join_request`, "
            "`plan_approval_request`, or `shutdown_request`; otherwise leave it unset."
        ),
        (
            "Use the current `team_name` exactly as provided here; do not guess or reuse a "
            "prior run's team name."
        ),
        (
            "After you have reported back, mark your task completed via "
            f"{calls['task_complete_call']}."
        ),
        "Report concisely with key findings and relevant data.",
    ]


def build_clawteam_spoke_task_description(
    agent_cfg: Any,
    task_goal: str,
    hub_role: str,
    *,
    team_name: str = "",
) -> str:
    """Build the seeded task-board description for a spoke/worker agent."""
    scope_text = (agent_cfg.system_prompt or agent_cfg.description or "").strip()
    protocol_lines = _clawteam_spoke_protocol_lines(
        agent_cfg,
        hub_role,
        team_name=team_name,
        task_id_placeholder="<this-task-id>",
    )
    numbered_protocol = "\n".join(
        f"{index}. {line}" for index, line in enumerate(protocol_lines, start=1)
    )
    return (
        f"Primary assignment channel: the shared task board for team {team_name}.\n\n"
        "Overall task goal:\n"
        f"{task_goal}\n\n"
        f"Your role: {agent_cfg.role}\n"
        f"Role scope:\n{scope_text}\n\n"
        "Execution protocol:\n"
        f"{numbered_protocol}\n"
    )


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
    task_list_call = "`task_list`"
    send_call = "`mailbox_send`"
    receive_call = "`mailbox_receive`"
    if team_name:
        task_list_call = f'`task_list(team_name="{team_name}")`'
        send_call = (
            f'`mailbox_send(team_name="{team_name}", from_agent="{hub_role}", '
            'to="<specialist>", content="...")`'
        )
        receive_call = (
            f'`mailbox_receive(team_name="{team_name}", agent_name="{hub_role}")`'
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
        "1. Analyze the task and inspect the shared task board for the specialist tasks that "
        f"have already been seeded via {task_list_call}.\n"
        "2. Treat the shared task board as the primary assignment channel. Specialist tasks are "
        "already pre-seeded on that task board.\n"
        f"3. Use {send_call} only for clarifications, nudges, missing IDs, or other follow-up "
        "context — not as the primary place where specialists first learn what to do.\n"
        "   Do not pass `msg_type` for normal assignment, follow-up, or result messages.\n"
        "   Leave `msg_type` unset so `mailbox_send` defaults to a plain `message`.\n"
        "   Only set `msg_type` when you intentionally need a known ClawTeam enum value "
        "such as `broadcast`, `idle`, `join_request`, `plan_approval_request`, or "
        "`shutdown_request`.\n"
        f"4. **Wait for your specialists to do the work.** Check your inbox via {receive_call} "
        "for their reports, and rely on the shared task board to see their seeded assignments "
        "and progress. \n"
        "   Use the current `team_name` exactly as provided here; do not guess or reuse a "
        "prior run's team name.\n"
        "5. Once specialists have reported back, synthesize their findings into "
        "the final comprehensive summary.\n"
        "6. You produce the final user-facing output.\n"
        f"{completion_step}"
    )


def build_clawteam_spoke_prompt(
    agent_cfg: Any,
    hub_role: str,
    *,
    team_name: str = "",
) -> str:
    """Build initial prompt for a spoke/worker agent in ClawTeam execution."""
    protocol_lines = _clawteam_spoke_protocol_lines(
        agent_cfg,
        hub_role,
        team_name=team_name,
    )
    numbered_protocol = "\n".join(
        f"{index}. {line}" for index, line in enumerate(protocol_lines, start=1)
    )
    return (
        f"# Your Role: {agent_cfg.role}\n\n"
        f"{agent_cfg.system_prompt or agent_cfg.description}\n\n"
        "## Protocol\n\n"
        f"{numbered_protocol}\n"
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
