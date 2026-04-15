from __future__ import annotations

from masp_bench.runner import BenchmarkRunner
from masp_bench.schemas.task import TaskConfig, ToolCatalog, ToolDef


class _DummyAdapter:
    framework_id = "dummy"

    async def run(self, ctx, initial_input):
        ctx.action_sink.emit_tool_call(
            agent_id="hub",
            agent_role="hub",
            tool_name="lookup",
            tool_args={"key": "k1"},
            tool_result="ok",
            raw_event={"source": "dummy"},
        )
        ctx.action_sink.emit_communication(
            agent_id="hub",
            agent_role="hub",
            target_agent="user",
            target_role="user",
            content="done",
            raw_event={"source": "dummy"},
            kind="user_output",
        )
        from masp_bench.frameworks.base import RunOutcome

        return RunOutcome(framework="dummy", final_output="done")


def test_framework_adapter_contract(monkeypatch) -> None:
    task = TaskConfig(
        task_id="dummy-t1",
        domain="finance",
        goal="dummy goal",
        agents=[{"role": "hub", "description": "hub"}],
        access_rules=[],
        success_checks=[],
    )
    catalog = ToolCatalog(
        domain="finance",
        name="finance",
        tools=[
            ToolDef(
                name="lookup",
                description="lookup",
                params={"key": "lookup key"},
                mock_response="ok",
            )
        ],
    )

    monkeypatch.setattr("masp_bench.runner.get_bank", lambda _domain: None)
    monkeypatch.setattr("masp_bench.runner.get_framework_adapter", lambda _fw: _DummyAdapter())

    runner = BenchmarkRunner(task, catalog, framework="dummy")
    trace = runner.run(run_id="dummy-1")

    assert trace.framework == "dummy"
    assert trace.task_completed is True
    assert trace.n_actions == 2
    assert trace.n_tool_calls == 1
    assert trace.n_communications == 1
