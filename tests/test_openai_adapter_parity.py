from __future__ import annotations

from masp_bench.runner import OpenAIAgentsRunner, framework_run_id_slug, next_run_id
from masp_bench.schemas.task import TaskConfig, ToolCatalog


def test_openai_runner_uses_openai_framework(monkeypatch) -> None:
    class _FakeOpenAIAdapter:
        framework_id = "openai_agents"

        async def run(self, ctx, initial_input):
            from masp_bench.frameworks.base import RunOutcome

            ctx.action_sink.emit_communication(
                agent_id="hub",
                agent_role="hub",
                target_agent="user",
                target_role="user",
                content="ok",
                kind="user_output",
            )
            return RunOutcome(framework="openai_agents", final_output="ok")

    task = TaskConfig(
        task_id="openai-t1",
        domain="finance",
        goal="goal",
        agents=[{"role": "hub", "description": "hub"}],
        access_rules=[],
        success_checks=[],
    )
    catalog = ToolCatalog(domain="finance", name="finance", tools=[])

    monkeypatch.setattr("masp_bench.runner.get_bank", lambda _domain: None)
    monkeypatch.setattr(
        "masp_bench.runner.get_framework_adapter",
        lambda _fw: _FakeOpenAIAdapter(),
    )

    runner = OpenAIAgentsRunner(task, catalog)
    trace = runner.run(run_id="oai-gpt-4.1-1")
    assert trace.framework == "openai_agents"
    assert trace.n_communications == 1


def test_framework_slug_and_run_id_patterns(tmp_path) -> None:
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "x_oai-gpt-4.1-2.json").write_text("{}")
    (traces / "x_ag-gpt-4.1-1.json").write_text("{}")

    assert framework_run_id_slug("openai_agents") == "oai"
    assert framework_run_id_slug("autogen") == "ag"
    assert next_run_id([traces], model="gpt-4.1", framework_slug="oai") == "oai-gpt-4.1-3"
    assert next_run_id([traces], model="gpt-4.1", framework_slug="ag") == "ag-gpt-4.1-2"
