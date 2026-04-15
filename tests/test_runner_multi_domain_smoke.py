from __future__ import annotations

from pathlib import Path

import pytest

from masp_bench.loader import load_task_with_tools
from masp_bench.runner import BenchmarkRunner


class _DummyAdapter:
    framework_id = "dummy"

    async def run(self, ctx, initial_input):
        from masp_bench.frameworks.base import RunOutcome

        ctx.action_sink.emit_communication(
            agent_id=ctx.task.agents[0].role,
            agent_role=ctx.task.agents[0].role,
            target_agent="user",
            target_role="user",
            content=f"{ctx.task.domain} done",
            kind="user_output",
        )
        return RunOutcome(framework="dummy", final_output=f"{ctx.task.domain} done")


@pytest.mark.parametrize(
    ("task_spec", "expected_domain"),
    [
        ("tasks/ecommerce/ec-t1.yaml", "ecommerce"),
        ("tasks/daily_life/dl-t1.yaml", "daily_life"),
    ],
)
def test_runner_smoke_across_two_domains(
    monkeypatch,
    task_spec: str,
    expected_domain: str,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    task, catalog = load_task_with_tools(
        task_spec,
        project_root=project_root,
        cwd=project_root,
    )
    assert task.domain == expected_domain

    monkeypatch.setattr("masp_bench.runner.get_bank", lambda _domain: None)
    monkeypatch.setattr("masp_bench.runner.get_framework_adapter", lambda _fw: _DummyAdapter())

    runner = BenchmarkRunner(task, catalog, framework="dummy")
    trace = runner.run(run_id=f"{expected_domain}-dummy-1")

    assert trace.framework == "dummy"
    assert trace.domain == expected_domain
    assert trace.n_communications == 1
    assert trace.error is None
