"""Google ADK adapter for HarnessAudit-Bench.

The adapter intentionally mirrors the OpenAI Agents SDK path: the hub delegates
with a MASP-controlled tool and specialist agents execute MASP domain tools.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, ClassVar

from .base import MASFrameworkAdapter, RunContext, RunOutcome


@dataclass
class ADKRuntime:
    """Shared runtime object held by ADK tool instances."""

    run_id: str
    action_sink: Any
    bank: Any
    workspace: Any
    agent_timeout: int
    spoke_agents: dict[str, Any] = field(default_factory=dict)
    _run_counter: int = 0

    async def run_agent(
        self,
        agent: Any,
        *,
        input_text: str,
        agent_role: str,
        max_turns: int,
    ) -> str:
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        self._run_counter += 1
        app_name = f"masp-adk-{self.run_id}-{agent_role}-{self._run_counter}"
        runner = InMemoryRunner(agent=agent, app_name=app_name)
        session = await runner.session_service.create_session(
            app_name=app_name,
            user_id=f"masp-{self.run_id}",
        )
        content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=input_text)],
        )
        final_output = ""

        async def consume() -> None:
            nonlocal final_output
            turn_count = 0
            async for event in runner.run_async(
                user_id=session.user_id,
                session_id=session.id,
                new_message=content,
            ):
                if event.author != agent_role:
                    continue
                if event.get_function_calls():
                    turn_count += 1
                    if turn_count > max_turns:
                        raise TimeoutError(
                            f"ADK agent {agent_role} exceeded max_turns={max_turns}"
                        )
                text = _event_text(event)
                if text:
                    final_output = text
                if event.is_final_response() and text:
                    final_output = text

        try:
            await asyncio.wait_for(consume(), timeout=self.agent_timeout)
        finally:
            await runner.close()
        return final_output


class ADKAdapter(MASFrameworkAdapter):
    """Run MASP tasks using Google ADK in-process."""

    framework_id: ClassVar[str] = "adk"

    def __init__(self, agent_timeout: int = 300, **_kwargs: Any) -> None:
        self._agent_timeout = agent_timeout

    async def run(
        self,
        ctx: RunContext,
        initial_input: str | list[dict[str, Any]],
    ) -> RunOutcome:
        try:
            from google.adk import Agent
        except ImportError as exc:
            raise RuntimeError(
                "Google ADK not installed. Install the Google ADK package first."
            ) from exc

        from .adk_openai_model import ADKOpenAIModel
        from .adk_tools import build_delegate_tool, build_domain_tools
        from .common import build_oai_hub_prompt, build_oai_spoke_prompt, extract_input_text

        hub_cfg = ctx.task.agents[0]
        hub_role = hub_cfg.role
        spoke_cfgs = ctx.task.agents[1:]
        runtime = ADKRuntime(
            run_id=ctx.run_id,
            action_sink=ctx.action_sink,
            bank=ctx.bank,
            workspace=ctx.workspace,
            agent_timeout=self._agent_timeout,
        )
        model = ADKOpenAIModel(
            model=ctx.model,
            api_key=os.environ.get("OPENAI_API_KEY") or None,
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
        )

        try:
            spoke_agents: dict[str, Any] = {}
            for agent_cfg in spoke_cfgs:
                spoke_agents[agent_cfg.role] = Agent(
                    name=agent_cfg.role,
                    description=agent_cfg.description or agent_cfg.role,
                    instruction=build_oai_spoke_prompt(agent_cfg, hub_role),
                    model=model,
                    tools=build_domain_tools(
                        ctx.catalog,
                        runtime=runtime,
                        agent_role=agent_cfg.role,
                        workspace=ctx.workspace,
                    ),
                )
            runtime.spoke_agents = spoke_agents

            hub = Agent(
                name=hub_role,
                description=hub_cfg.description or hub_role,
                instruction=build_oai_hub_prompt(hub_cfg, spoke_cfgs, ctx.task.goal),
                model=model,
                tools=[
                    *build_domain_tools(
                        ctx.catalog,
                        runtime=runtime,
                        agent_role=hub_role,
                        workspace=ctx.workspace,
                    ),
                    build_delegate_tool(
                        runtime=runtime,
                        hub_role=hub_role,
                        max_spoke_turns=10,
                    ),
                ],
            )

            input_text = extract_input_text(initial_input) or ctx.task.goal
            final_output = await runtime.run_agent(
                hub,
                input_text=input_text,
                agent_role=hub_role,
                max_turns=ctx.max_turns,
            )
        except Exception as exc:
            return RunOutcome(
                framework=self.framework_id,
                final_output="",
                error=str(exc),
            )

        ctx.action_sink.emit_communication(
            agent_id=hub_role,
            agent_role=hub_role,
            target_agent="user",
            target_role="user",
            content=final_output,
            raw_event={
                "framework": "adk",
                "communication_kind": "user_output",
            },
        )
        return RunOutcome(framework=self.framework_id, final_output=final_output)


def _event_text(event: Any) -> str:
    if not event.content or not event.content.parts:
        return ""
    return "\n".join(
        part.text for part in event.content.parts if part.text and not part.thought
    ).strip()
