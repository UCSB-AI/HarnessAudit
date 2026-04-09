"""Task runner — execute multi-agent tasks via OpenAI Agents SDK and capture traces."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, cast

from masp_bench.schemas.actions import ObservableAction
from masp_bench.schemas.task import TaskConfig, ToolCatalog, ToolDef
from masp_bench.schemas.trace import ExecutionTrace
from masp_bench.trace.events import (
    AgentInfo, CommunicationEvent, HandoffEvent, ToolCallEvent, TraceStart,
)
from masp_bench.trace.writer import TraceWriter


class OpenAIAgentsRunner:
    """Run multi-agent tasks using the OpenAI Agents SDK.

    Creates Agent objects with mock tool functions, sets up handoffs,
    captures all tool calls and inter-agent communications via RunHooks,
    and returns an ExecutionTrace.
    """

    def __init__(
        self,
        task: TaskConfig,
        catalog: ToolCatalog,
        model: str = "gpt-4.1",
        trace_writer: TraceWriter | None = None,
    ):
        self.task = task
        self.catalog = catalog
        self.model = model
        self._trace_writer = trace_writer
        self._name_to_role: dict[str, str] = {}

    def run(self, defense_mode: str = "monitor", run_id: str | None = None) -> ExecutionTrace:
        """Synchronous entry point."""
        return asyncio.run(self._run_async(defense_mode, run_id=run_id))

    async def _run_async(self, defense_mode: str, run_id: str | None = None) -> ExecutionTrace:
        from agents import Agent, Runner, RunHooks, handoff
        from agents.tool import FunctionTool

        from datetime import datetime, timezone

        run_id = run_id or f"oai-{uuid.uuid4().hex[:8]}"
        trace_writer = self._trace_writer
        run_started_at = datetime.now(timezone.utc)

        # --- Build tool functions from catalog ---
        tool_fns: dict[str, FunctionTool] = {}
        for tool_def in self.catalog.tools:
            tool_fns[tool_def.name] = self._make_tool(tool_def)

        # --- Build agents ---
        sdk_agents: dict[str, Agent] = {}
        for agent_cfg in self.task.agents:
            agent_tools = [
                tool_fns[tn] for tn in agent_cfg.visible_tools
                if tn in tool_fns
            ]
            sdk_agents[agent_cfg.role] = Agent(
                name=agent_cfg.role,
                instructions=agent_cfg.system_prompt or agent_cfg.description,
                tools=agent_tools,
                model=self.model,
            )
            self._name_to_role[agent_cfg.role] = agent_cfg.role

        # --- Handoffs: first agent (hub) can handoff to all others ---
        hub_role = self.task.agents[0].role
        hub_agent = sdk_agents[hub_role]
        for role, agent in sdk_agents.items():
            if role != hub_role:
                hub_agent.handoffs.append(handoff(agent))

        # --- Emit TraceStart ---
        if trace_writer:
            trace_writer.write_event(TraceStart(
                trace_id=run_id,
                task_id=self.task.task_id,
                domain=self.task.domain,
                framework="openai_agents",
                model=self.model,
                defense_mode=defense_mode,
                agents=[
                    AgentInfo(
                        agent_id=ac.role, role=ac.role,
                        visible_tools=ac.visible_tools,
                    )
                    for ac in self.task.agents
                ],
                n_rules=len(self.task.access_rules),
                n_canaries=len(self.task.canary_tokens),
            ))

        # --- Trace capture hooks ---
        captured_actions: list[ObservableAction] = []
        captured_seq = [0]
        name_to_role = self._name_to_role

        class TraceHooks(RunHooks):  # type: ignore[type-arg]
            async def on_tool_end(self, context: Any, agent: Any, tool: Any, result: str) -> None:
                captured_seq[0] += 1
                role = name_to_role.get(agent.name, agent.name)
                # Read args directly from context to avoid key-collision
                # when the same tool is called in parallel.
                try:
                    args_str = cast(Any, context).tool_arguments
                except Exception:
                    args_str = "{}"
                try:
                    args_dict = json.loads(args_str) if isinstance(args_str, str) else {}
                except (json.JSONDecodeError, TypeError):
                    args_dict = {}
                captured_actions.append(ObservableAction(
                    sequence_num=captured_seq[0], run_id=run_id,
                    surface="tool_call",
                    agent_id=agent.name, agent_role=role,
                    tool_name=tool.name, tool_args=args_dict,
                    tool_result=str(result)[:500],
                ))
                if trace_writer:
                    trace_writer.write_event(ToolCallEvent(
                        trace_id=run_id, sequence_num=captured_seq[0],
                        agent_id=agent.name, agent_role=role,
                        tool_name=tool.name, tool_args=args_dict,
                        tool_result=str(result)[:500],
                    ))

            async def on_handoff(self, context: Any, from_agent: Any, to_agent: Any) -> None:
                captured_seq[0] += 1
                from_role = name_to_role.get(from_agent.name, from_agent.name)
                to_role = name_to_role.get(to_agent.name, to_agent.name)
                captured_actions.append(ObservableAction(
                    sequence_num=captured_seq[0], run_id=run_id,
                    surface="communication",
                    agent_id=from_agent.name, agent_role=from_role,
                    target_agent=to_agent.name, target_role=to_role,
                    content=f"[handoff from {from_role} to {to_role}]",
                ))
                if trace_writer:
                    trace_writer.write_event(HandoffEvent(
                        trace_id=run_id, sequence_num=captured_seq[0],
                        from_agent_id=from_agent.name, from_role=from_role,
                        to_agent_id=to_agent.name, to_role=to_role,
                    ))

        hooks = TraceHooks()

        # --- Run ---
        result = await Runner.run(
            starting_agent=hub_agent,
            input=self.task.goal,
            max_turns=20,
            hooks=hooks,
        )

        # --- Final output → communication to user ---
        final_text = str(result.final_output) if result.final_output else ""
        captured_seq[0] += 1
        last_role = result.last_agent.name if result.last_agent else hub_role
        captured_actions.append(ObservableAction(
            sequence_num=captured_seq[0], run_id=run_id,
            surface="communication",
            agent_id=last_role, agent_role=last_role,
            target_agent="user", target_role="user",
            content=final_text,
        ))
        if trace_writer:
            trace_writer.write_event(CommunicationEvent(
                trace_id=run_id, sequence_num=captured_seq[0],
                agent_id=last_role, agent_role=last_role,
                target_agent="user", target_role="user",
                content=final_text,
            ))

        # --- Evaluate success_checks ---
        task_completed = self._check_success(captured_actions, final_text)

        trace = ExecutionTrace(
            run_id=run_id, task_id=self.task.task_id, domain=self.task.domain,
            framework="openai_agents", llm=self.model, defense_mode=defense_mode,
            actions=captured_actions, decisions=[], task_completed=task_completed,
            started_at=run_started_at,
        )
        trace.finalize()
        return trace

    def _make_tool(self, tool_def: ToolDef) -> Any:
        """Create an SDK FunctionTool from a ToolDef.

        If the tool has fixture_data + fixture_key, responses are dynamically
        built by looking up the arg value in the fixture dict.
        Otherwise falls back to static mock_response.
        """
        from agents.tool import FunctionTool

        # Canary token injection (appended to every response variant)
        canary_suffix = ""
        for canary_label in tool_def.canary_tokens:
            for ct in self.task.canary_tokens:
                if ct.label == canary_label:
                    canary_suffix += f"\n[{ct.token}]"

        fixture_data = tool_def.fixture_data
        fixture_key = tool_def.fixture_key
        static_response = tool_def.mock_response or f"{tool_def.name}: OK"

        async def _invoke(_ctx: Any, args_json: str) -> str:
            try:
                args = json.loads(args_json) if args_json else {}
            except (json.JSONDecodeError, TypeError):
                args = {}

            # --- Fixture-based dynamic response ---
            if fixture_data and fixture_key:
                lookup_val = str(args.get(fixture_key, ""))
                # Support comma-separated multi-lookup (e.g., tickers="AAPL,MSFT")
                keys = [k.strip() for k in lookup_val.split(",") if k.strip()]
                if not keys:
                    keys = [lookup_val]

                header = fixture_data.get("_header", "")
                footer = fixture_data.get("_footer", "")
                default = fixture_data.get("_default", "No data found.")

                lines: list[str] = []
                if header:
                    lines.append(header)
                for key in keys:
                    entry = fixture_data.get(key)
                    if entry is not None:
                        lines.append(entry)
                    else:
                        lines.append(f"{key}: {default}")
                if footer:
                    lines.append(footer)

                result = "\n".join(lines)
                return result + canary_suffix

            # --- Static fallback ---
            return static_response + canary_suffix

        properties = {}
        required = []
        for pname in tool_def.params:
            properties[pname] = {"type": "string", "description": pname}
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

    def _check_success(
        self, actions: list[ObservableAction], final_output: str,
    ) -> bool:
        """Evaluate success_checks from the task config.

        Supported check types:
          - tool_called: verify a tool was called at least min_calls times
          - final_output_contains: verify keywords appear in the final output
        """
        if not self.task.success_checks:
            return True  # no checks defined → assume success

        for check in self.task.success_checks:
            check_type = check.get("type")

            if check_type == "tool_called":
                tool_name = check.get("tool_name", "")
                min_calls = check.get("min_calls", 1)
                actual = sum(
                    1 for a in actions
                    if a.surface == "tool_call" and a.tool_name == tool_name
                )
                if actual < min_calls:
                    return False

            elif check_type == "final_output_contains":
                keywords = check.get("keywords", [])
                output_lower = final_output.lower()
                for kw in keywords:
                    if kw.lower() not in output_lower:
                        return False

        return True
