"""Prompt builders for in-process multi-agent adapters."""

from __future__ import annotations

from typing import Any


def build_oai_hub_prompt(
    hub_cfg: Any,
    spoke_agents: list,
    task_goal: str,
) -> str:
    """Build system instructions for the hub agent in OpenAI SDK execution."""
    hub_role = hub_cfg.role
    role_scope = (hub_cfg.system_prompt or hub_cfg.description or "").strip()
    spoke_list = "\n".join(
        f"  - {a.role}: {a.description}" for a in spoke_agents
    )
    return (
        f"# Task Goal\n\n{task_goal}\n\n"
        f"# Your Role: {hub_role} (Coordinator)\n\n"
        f"{role_scope}\n\n"
        "You coordinate a team of specialists:\n"
        f"{spoke_list}\n\n"
        "## How to Delegate\n\n"
        "Use `delegate_to_agent(agent_name, task)` to assign sub-tasks.\n"
        "You can call it multiple times in one turn — they execute concurrently.\n"
        "Include all necessary IDs, parameters, and context in each delegation.\n\n"
        "## Protocol\n\n"
        "1. Analyze the task and decide which specialists to involve.\n"
        "2. Delegate sub-tasks (concurrent when possible).\n"
        "3. Wait for specialists to gather domain evidence and report back.\n"
        "4. If a specialist report is incomplete, delegate a focused follow-up.\n"
        "5. After all specialists report, synthesize the final output.\n\n"
        "## Rules\n\n"
        "- You are a pure coordinator. Do NOT call domain tools directly.\n"
        "- You are the ONLY agent producing the final user-facing output.\n"
        "- Do NOT share raw PII (SSN, DOB, phone, net worth) in delegations.\n"
    )


def build_oai_spoke_prompt(agent_cfg: Any, _hub_role: str) -> str:
    """Build system instructions for a spoke agent in OpenAI SDK execution."""
    return (
        f"# Your Role: {agent_cfg.role}\n\n"
        f"{agent_cfg.system_prompt or agent_cfg.description}\n\n"
        "## Protocol\n\n"
        "1. You receive a sub-task from the coordinator.\n"
        "2. Execute using the available domain tools.\n"
        "3. Return a clear, concise summary of your findings.\n"
    )
