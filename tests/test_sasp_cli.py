from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from masp_bench.runner import next_run_id
from sasp_bench import cli


def test_sasp_cli_defaults_to_openclaw_local_framework() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["run", "tasks/finance/sa-fin-t1.yaml"])

    assert args.framework == "openclaw_local"
    assert args.model == "anthropic/claude-sonnet-4-6"


def test_sasp_cli_main_does_not_require_openai_api_key(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_cmd_run(args) -> None:
        captured["command"] = args.command
        captured["framework"] = args.framework
        captured["model"] = args.model

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(cli, "cmd_run", _fake_cmd_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["sasp-bench", "run", "tasks/finance/sa-fin-t1.yaml"],
    )

    cli.main()

    assert captured == {
        "command": "run",
        "framework": "openclaw_local",
        "model": "anthropic/claude-sonnet-4-6",
    }


def test_next_run_id_sanitizes_provider_qualified_model_names() -> None:
    run_id = next_run_id(
        model="anthropic/claude-sonnet-4-6",
        framework_slug="ol",
        harness_slug="openclaw",
    )

    assert "/" not in run_id
    assert run_id.startswith("ol-openclaw-anthropic-claude-sonnet-4-6-")


def test_sasp_cli_run_uses_openclaw_harness_slug(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    task = SimpleNamespace(
        task_id="sa-fin-t1",
        domain="finance",
        agents=[SimpleNamespace(role="investment_analyst")],
        access_rules=[],
        metadata={},
        fixture=None,
        goal="Review the account.",
    )
    catalog = SimpleNamespace(tools=[])

    class _FakeRunner:
        def __init__(self, *args, **kwargs) -> None:
            captured["runner_harness"] = kwargs["harness"]

        def run(self, run_id: str):
            captured["runner_run_id"] = run_id
            return SimpleNamespace(
                run_id=run_id,
                framework="openclaw_local",
                llm="anthropic/claude-sonnet-4-6",
                n_actions=0,
                n_tool_calls=0,
                n_communications=0,
                error=None,
                task_completed=False,
                actions=[],
                duration_seconds=0.0,
            )

        def dump_bank_to_file(self, _path) -> bool:
            return False

        def close_bank(self) -> None:
            return None

    def _fake_next_run_id(model: str, **kwargs) -> str:
        captured["next_run_id_model"] = model
        captured["next_run_id_kwargs"] = kwargs
        return "ol-openclaw-anthropic-claude-sonnet-4-6-test1234"

    monkeypatch.setattr(
        cli,
        "resolve_sa_task_path",
        lambda *args, **kwargs: tmp_path / "task.yaml",
    )
    monkeypatch.setattr(cli, "load_sa_task_with_tools", lambda _path: (task, catalog))
    monkeypatch.setattr(cli, "BenchmarkRunner", _FakeRunner)
    monkeypatch.setattr(cli, "next_run_id", _fake_next_run_id)
    monkeypatch.setattr(cli, "check_trace", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        cli,
        "compute_metrics",
        lambda *args, **kwargs: SimpleNamespace(
            access_score=1.0,
            op_privacy_score=None,
            info_privacy_score=None,
            tsr=0,
            pqsr=0,
            violations_by_code={},
            violations_by_severity={},
        ),
    )

    args = cli.build_parser().parse_args(["run", "tasks/finance/sa-fin-t1.yaml"])
    args.output = str(tmp_path / "results")
    args.trace_dir = str(tmp_path / "traces")

    cli.cmd_run(args)

    assert captured["runner_harness"] == "openclaw"
    assert captured["runner_run_id"] == "ol-openclaw-anthropic-claude-sonnet-4-6-test1234"
    assert captured["next_run_id_model"] == "anthropic/claude-sonnet-4-6"
    assert captured["next_run_id_kwargs"] == {
        "framework_slug": "ol",
        "harness_slug": "openclaw",
    }
