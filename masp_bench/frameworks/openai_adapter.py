"""OpenAI Agents SDK adapter for unified AAP execution."""

from __future__ import annotations

import json
from typing import Any, cast

from pydantic import BaseModel, Field

from .base import MASFrameworkAdapter, RunContext, RunOutcome
from .common import (
    build_handoff_targets,
    build_hub_orchestration_suffix,
    build_spoke_suffix,
    extract_handoff_context_raw,
    extract_handoff_note,
)


def _build_openai_tool(tool_runtime: Any, tool_def: Any) -> Any:
    """Create OpenAI Agents SDK FunctionTool using shared ToolRuntime dispatch."""
    from agents.tool import FunctionTool

    async def _invoke(_ctx: Any, args_json: str) -> str:
        args = tool_runtime.parse_args_json(args_json)
        outcome = await tool_runtime.invoke(tool_def=tool_def, tool_args=args)
        return outcome.output

    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    for pname, pdesc in tool_def.params.items():
        properties[pname] = {"type": "string", "description": pdesc or pname}
        required.append(pname)

    schema = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }

    return FunctionTool(
        name=tool_def.name,
        description=tool_def.description or tool_def.name,
        params_json_schema=schema,
        on_invoke_tool=_invoke,
        strict_json_schema=True,
    )


class HandoffPayload(BaseModel):
    """Required payload for each OpenAI handoff call."""

    task_assignment: str = Field(
        ...,
        description=(
            "Concrete, actionable instruction for receiving agent. Must be non-empty."
        ),
    )


class OpenAIAdapter(MASFrameworkAdapter):
    """Run tasks using the OpenAI Agents SDK and emit normalized actions."""

    framework_id = "openai_agents"

    async def run(self, ctx: RunContext, initial_input: str | list[dict[str, Any]]) -> RunOutcome:
        from agents import Agent, RunHooks, Runner, handoff

        tool_fns = {
            tool_def.name: _build_openai_tool(ctx.tool_runtime, tool_def)
            for tool_def in ctx.catalog.tools
        }
        all_catalog_tools = [tool_fns[td.name] for td in ctx.catalog.tools]

        hub_role = ctx.task.agents[0].role
        sdk_agents: dict[str, Agent] = {}
        name_to_role: dict[str, str] = {}
        for idx, agent_cfg in enumerate(ctx.task.agents):
            base_prompt = agent_cfg.system_prompt or agent_cfg.description
            suffix = (
                build_hub_orchestration_suffix(hub_role)
                if idx == 0
                else build_spoke_suffix(hub_role)
            )
            sdk_agents[agent_cfg.role] = Agent(
                name=agent_cfg.role,
                instructions=base_prompt + suffix,
                tools=all_catalog_tools,
                model=ctx.model,
            )
            name_to_role[agent_cfg.role] = agent_cfg.role

        async def _handoff_payload_noop(_context: Any, _payload: HandoffPayload) -> None:
            return None

        # Keep full reachability; policy layer enforces allowed communication.
        handoff_targets = build_handoff_targets(list(sdk_agents))
        for src_role, src_agent in sdk_agents.items():
            for dst_role in handoff_targets[src_role]:
                src_agent.handoffs.append(
                    handoff(
                        sdk_agents[dst_role],
                        input_type=HandoffPayload,
                        on_handoff=_handoff_payload_noop,
                    )
                )

        pending_handoff: list[dict[str, Any] | None] = [None]
        bank = ctx.bank

        class TraceHooks(RunHooks):  # type: ignore[type-arg]
            async def on_tool_start(self, context: Any, agent: Any, tool: Any) -> None:
                if bank is None:
                    return
                role = name_to_role.get(agent.name, agent.name)
                try:
                    bank.set_current_agent(role)
                except Exception:
                    return

            async def on_llm_start(
                self, context: Any, agent: Any, system_prompt: Any, input_items: Any
            ) -> None:
                if pending_handoff[0] is None:
                    return
                handoff_meta = pending_handoff[0]
                pending_handoff[0] = None
                handoff_context_raw = extract_handoff_context_raw(input_items)
                handoff_note = extract_handoff_note(input_items)
                ctx.action_sink.emit_handoff(
                    from_agent_id=handoff_meta["from_id"],
                    from_role=handoff_meta["from_role"],
                    to_agent_id=handoff_meta["to_id"],
                    to_role=handoff_meta["to_role"],
                    handoff_note=handoff_note,
                    handoff_context_raw=handoff_context_raw,
                    raw_event={
                        "communication_kind": "handoff",
                        "capture_mode": "note_plus_raw",
                        "handoff_note": handoff_note,
                        "handoff_context_raw": handoff_context_raw,
                    },
                    sequence_num=handoff_meta["seq"],
                )

            async def on_tool_end(self, context: Any, agent: Any, tool: Any, result: str) -> None:
                agent_name = agent.name or ""
                role = name_to_role.get(agent_name, agent_name)
                try:
                    args_str = cast(Any, context).tool_arguments
                except Exception:
                    args_str = "{}"
                try:
                    args_dict = json.loads(args_str) if isinstance(args_str, str) else {}
                except (json.JSONDecodeError, TypeError):
                    args_dict = {}
                ctx.action_sink.emit_tool_call(
                    agent_id=agent_name,
                    agent_role=role,
                    tool_name=tool.name,
                    tool_args=args_dict,
                    tool_result=str(result),
                    raw_event={"tool_source": "catalog_dispatch"},
                )

            async def on_handoff(self, context: Any, from_agent: Any, to_agent: Any) -> None:
                from_role = name_to_role.get(from_agent.name, from_agent.name)
                to_role = name_to_role.get(to_agent.name, to_agent.name)
                pending_handoff[0] = {
                    "seq": ctx.action_sink.reserve_sequence(),
                    "from_id": from_agent.name,
                    "from_role": from_role,
                    "to_id": to_agent.name,
                    "to_role": to_role,
                }

        hooks = TraceHooks()
        try:
            result = await Runner.run(
                starting_agent=sdk_agents[hub_role],
                input=cast(Any, initial_input),
                max_turns=ctx.max_turns,
                hooks=hooks,
            )
            final_text = str(result.final_output) if result.final_output else ""
            last_role = result.last_agent.name if result.last_agent else hub_role
            ctx.action_sink.emit_communication(
                agent_id=last_role,
                agent_role=last_role,
                target_agent="user",
                target_role="user",
                content=final_text,
                raw_event={
                    "communication_kind": "user_output",
                    "capture_mode": "final_output",
                },
                kind="user_output",
            )
            return RunOutcome(framework=self.framework_id, final_output=final_text)
        except Exception as exc:
            if "Max turns" not in str(exc) and "max_turns" not in str(exc).lower():
                raise
            return RunOutcome(framework=self.framework_id, final_output="", error=str(exc))
