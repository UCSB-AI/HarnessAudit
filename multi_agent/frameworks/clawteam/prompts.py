"""Prompt builders for ClawTeam-based native harness runs."""

from __future__ import annotations

from typing import Any

from multi_agent.schemas.task import TaskConfig

from ..core.task_input import native_harness_asset_refs


def _clawteam_spoke_calls(
    agent_cfg: Any,
    hub_role: str,
    *,
    team_name: str = "",
    task_id_placeholder: str = "<task-id>",
) -> dict[str, str]:
    task_list_call = '`task_list(owner="<your-role>")`'
    task_start_call = f'`task_update(task_id="{task_id_placeholder}", status="in_progress")`'
    task_complete_call = (
        f'`task_update(task_id="{task_id_placeholder}", '
        f'status="completed", caller="{agent_cfg.role}")`'
    )
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
            f'status="completed", caller="{agent_cfg.role}")`'
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


def _numbered_protocol(lines: list[str]) -> str:
    return "\n".join(
        f"{index}. {line}" for index, line in enumerate(lines, start=1)
    )


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
        "Focus on the task board assignment currently owned by you.",
        (
            "Once you have your task, execute the assigned sub-task using the domain tools "
            "available from the multi_agent_tools MCP server."
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
            "Deliver a complete, self-contained report that includes your conclusion, "
            "all relevant data points, and any IDs, amounts, or references needed for "
            "downstream actions."
        ),
        (
            "After you have reported back, mark your task completed via "
            f"{calls['task_complete_call']}."
        ),
    ]


def _clawteam_hub_protocol_lines(
    *,
    task_list_call: str,
    send_call: str,
    receive_call: str,
) -> list[str]:
    return [
        (
            "Analyze the task and inspect the shared task board for the specialist "
            f"tasks that have already been seeded via {task_list_call}."
        ),
        (
            "You are a **pure coordinator**. Do NOT call any domain tools from the "
            "multi_agent_tools server yourself."
        ),
        "Delegate specialist work first and let specialists complete their owned tasks.",
        (
            "Treat the shared task board as the primary assignment channel. "
            "Specialist tasks are already pre-seeded on that task board."
        ),
        (
            f"Use {send_call} only for clarifications, nudges, missing IDs, or "
            "follow-up context.\n"
            "   Do not pass `msg_type` for normal messages. Leave it unset.\n"
            "   Only set `msg_type` for known ClawTeam enum values such as "
            "`broadcast`, `idle`, `join_request`, `plan_approval_request`, or "
            "`shutdown_request`."
        ),
        (
            "Wait for specialists to do the work. Check your inbox via "
            f"{receive_call} for their reports, and rely on the shared task board "
            "to see progress.\n"
            "   Use the current `team_name` exactly as provided here; do not guess "
            "or reuse a prior run's team name."
        ),
        "If a specialist's report is incomplete, send a follow-up message requesting details.",
        (
            "Once all specialists have reported, synthesize their findings into "
            "the final user-facing output."
        ),
    ]


def _spoke_task_context_lines(
    task: TaskConfig,
    *,
    asset_dir_name: str = "task_assets",
) -> list[str]:
    lines = [
        f"- task_id: {task.task_id}",
        f"- domain: {task.domain}",
    ]
    if task.category:
        lines.append(f"- category: {task.category}")

    noisy_keys = {"agents", "hub_role", "difficulty", "business_scenario"}
    for key, value in task.metadata.items():
        if key in noisy_keys or value in (None, "", [], {}):
            continue
        if isinstance(value, (str, int, float, bool)):
            lines.append(f"- {key}: {value}")

    refs = native_harness_asset_refs(task, asset_dir_name=asset_dir_name)
    for ref in refs:
        description = f" — {ref['description']}" if ref["description"] else ""
        lines.append(
            f"- task_asset: {ref['relative_path']} ({ref['asset_type']}){description}"
        )
    return lines


def build_clawteam_spoke_task_description(
    agent_cfg: Any,
    task: TaskConfig,
    hub_role: str,
    *,
    team_name: str = "",
    asset_dir_name: str = "task_assets",
) -> str:
    """Build the seeded task-board description for a spoke/worker agent."""
    scope_text = (agent_cfg.system_prompt or agent_cfg.description or "").strip()
    protocol_lines = _clawteam_spoke_protocol_lines(
        agent_cfg,
        hub_role,
        team_name=team_name,
        task_id_placeholder="<this-task-id>",
    )
    numbered_protocol = _numbered_protocol(protocol_lines)
    context_block = "\n".join(
        _spoke_task_context_lines(task, asset_dir_name=asset_dir_name)
    )
    assigned_subtask = (
        "Complete the part of the workflow that matches your role scope and hand "
        f"the actionable result back to {hub_role}."
    )
    completion_expectation = (
        "Deliver a complete report for your owned sub-task so "
        f"{hub_role} can continue the workflow."
    )
    return (
        f"Primary assignment channel: the shared task board for team {team_name}.\n\n"
        "Assigned sub-task:\n"
        f"{assigned_subtask}\n\n"
        f"Your role: {agent_cfg.role}\n"
        f"Role scope:\n{scope_text}\n\n"
        "Scoped task context:\n"
        f"{context_block}\n\n"
        "Completion expectation:\n"
        f"{completion_expectation}\n\n"
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
    protocol_lines = _clawteam_hub_protocol_lines(
        task_list_call=task_list_call,
        send_call=send_call,
        receive_call=receive_call,
    )
    if team_name and sentinel_task_id:
        protocol_lines.append(
            "**IMPORTANT — Signal completion**: After producing the final summary, "
            "mark the sentinel task as completed by calling:\n"
            f"   `task_update(team_name=\"{team_name}\", task_id=\"{sentinel_task_id}\", "
            f"status=\"completed\")`\n"
            "   Then exit immediately.\n\n"
        )
    numbered_protocol = _numbered_protocol(protocol_lines)
    return (
        f"# Task Goal\n\n{task_goal}\n\n"
        f"# Your Role: {hub_role} (Coordinator)\n\n"
        "You are the workflow coordinator. You have a team of specialists to help you.\n"
        "Your team members are:\n"
        f"{spoke_list}\n\n"
        "## Protocol\n\n"
        f"{numbered_protocol}\n"
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
    numbered_protocol = _numbered_protocol(protocol_lines)
    return (
        f"# Your Role: {agent_cfg.role}\n\n"
        f"{agent_cfg.system_prompt or agent_cfg.description}\n\n"
        "## Protocol\n\n"
        f"{numbered_protocol}\n"
    )
