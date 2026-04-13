from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace

from masp_bench.loader import synthesize_allowed_tool_rules
from masp_bench.runner import OpenAIAgentsRunner
from masp_bench.schemas.task import AgentConfig, TaskConfig, ToolCatalog, ToolDef
from masp_bench.trace.writer import TraceWriter


def test_synthesized_tool_call_rules_only_set_tool_match_field() -> None:
    task = TaskConfig(
        task_id="fin-test",
        domain="finance",
        goal="Test synthesized rules",
        agents=[
            AgentConfig(
                role="research_analyst",
                description="Reviews market data",
                allowed_tools=["get_market_data"],
            )
        ],
        access_rules=[],
    )
    catalog = ToolCatalog(
        domain="finance",
        name="Finance",
        tools=[
            ToolDef(name="get_market_data"),
            ToolDef(name="execute_trade"),
        ],
    )

    synthesize_allowed_tool_rules(task, catalog)

    execute_trade_rule = next(
        rule
        for rule in task.access_rules
        if rule.synthesized and rule.match.tool == "execute_trade"
    )
    assert execute_trade_rule.match.model_fields_set == {"tool"}


def test_runner_run_tolerates_missing_task_canary_tokens(
    monkeypatch, tmp_path
) -> None:
    fake_agents = types.ModuleType("agents")
    fake_agents_tool = types.ModuleType("agents.tool")

    class FakeFunctionTool:
        def __init__(
            self,
            *,
            name: str,
            description: str,
            params_json_schema: dict,
            on_invoke_tool,
            strict_json_schema: bool,
        ) -> None:
            self.name = name
            self.description = description
            self.params_json_schema = params_json_schema
            self.on_invoke_tool = on_invoke_tool
            self.strict_json_schema = strict_json_schema

    class FakeAgent:
        def __init__(self, *, name: str, instructions: str, tools: list, model: str) -> None:
            self.name = name
            self.instructions = instructions
            self.tools = tools
            self.model = model
            self.handoffs: list[object] = []

    class FakeRunHooks:
        pass

    class FakeRunner:
        @staticmethod
        async def run(*, starting_agent, input: str, max_turns: int, hooks) -> SimpleNamespace:
            return SimpleNamespace(final_output="done", last_agent=starting_agent)

    def fake_handoff(agent, *, input_type=None, on_handoff=None) -> dict[str, object]:
        return {
            "agent": agent,
            "input_type": input_type,
            "on_handoff": on_handoff,
        }

    fake_agents.Agent = FakeAgent
    fake_agents.RunHooks = FakeRunHooks
    fake_agents.Runner = FakeRunner
    fake_agents.handoff = fake_handoff
    fake_agents_tool.FunctionTool = FakeFunctionTool

    monkeypatch.setitem(sys.modules, "agents", fake_agents)
    monkeypatch.setitem(sys.modules, "agents.tool", fake_agents_tool)
    monkeypatch.setattr("masp_bench.runner.get_bank", lambda _domain: None)

    task = TaskConfig(
        task_id="sde-test",
        domain="software_engineering",
        goal="Return done",
        agents=[
            AgentConfig(
                role="tech_lead",
                description="Coordinates the task",
                allowed_tools=["read_source_code"],
            )
        ],
        access_rules=[],
    )
    catalog = ToolCatalog(
        domain="software_engineering",
        name="SDE",
        tools=[
            ToolDef(
                name="read_source_code",
                mock_response="OK",
                canary_tokens=["legacy_marker"],
            )
        ],
    )
    trace_path = tmp_path / "trace.jsonl"
    runner = OpenAIAgentsRunner(
        task,
        catalog,
        model="fake-model",
        trace_writer=TraceWriter(trace_path),
    )

    trace = runner.run(run_id="test-run")

    assert trace.error is None
    assert trace.n_actions == 1

    events = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert events[0]["type"] == "trace_start"
    assert events[0]["n_canaries"] == 0
