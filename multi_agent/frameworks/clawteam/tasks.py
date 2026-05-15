"""Task-board seeding helpers for ClawTeam adapter execution."""

from __future__ import annotations

from typing import Any

from .prompts import build_clawteam_spoke_task_description


def _spoke_task_subject(agent_cfg: Any) -> str:
    summary = (agent_cfg.description or agent_cfg.system_prompt or "").strip()
    if summary and not summary.endswith("."):
        summary = f"{summary}."
    if not summary:
        summary = "Complete your assigned specialist work."
    return f"{agent_cfg.role}: {summary}"


def _spoke_task_description(
    agent_cfg: Any,
    task: Any,
    hub_role: str,
    *,
    team_name: str,
) -> str:
    return build_clawteam_spoke_task_description(
        agent_cfg,
        task,
        hub_role,
        team_name=team_name,
    )


def seed_spoke_tasks(
    task_store: Any,
    spoke_agents: list[Any],
    *,
    task: Any,
    hub_role: str,
    team_name: str,
) -> dict[str, str]:
    """Create one seeded task-board assignment per specialist."""
    task_ids: dict[str, str] = {}
    for agent_cfg in spoke_agents:
        task_item = task_store.create(
            subject=_spoke_task_subject(agent_cfg),
            description=_spoke_task_description(
                agent_cfg,
                task,
                hub_role,
                team_name=team_name,
            ),
            owner=agent_cfg.role,
            metadata={
                "assignment_mode": "task_board_first",
                "hub_role": hub_role,
                "role": agent_cfg.role,
            },
        )
        task_ids[agent_cfg.role] = task_item.id
    return task_ids
