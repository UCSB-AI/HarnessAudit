from __future__ import annotations

import asyncio
import json
from pathlib import Path

from masp_bench.frameworks import get_framework_adapter
from masp_bench.frameworks.action_sink import ActionSink
from masp_bench.frameworks.base import RunContext
from masp_bench.frameworks.openclaw_local_adapter import OpenClawLocalAdapter
from masp_bench.frameworks.openclaw_local_runtime import OpenClawLocalRuntime
from masp_bench.schemas.task import AgentConfig, TaskConfig, ToolCatalog, ToolDef


def _build_task(*, roles: list[str]) -> TaskConfig:
    return TaskConfig(
        task_id="sa-fin-t1",
        domain="finance",
        goal="Analyze the portfolio and reply with a recommendation.",
        agents=[
            AgentConfig(
                role=role,
                description=f"{role} specialist",
                system_prompt=f"You are the {role}.",
                allowed_tools=["lookup_account"],
            )
            for role in roles
        ],
        access_rules=[],
        success_checks=[],
        metadata={},
    )


def _build_catalog() -> ToolCatalog:
    return ToolCatalog(
        domain="finance",
        name="Finance",
        tools=[
            ToolDef(
                name="lookup_account",
                description="Lookup an account record.",
                params={"account_id": "str"},
            )
        ],
    )


def test_framework_registry_exposes_openclaw_local_adapter() -> None:
    adapter = get_framework_adapter(
        "openclaw_local",
        agent_timeout=17,
        harness="openclaw",
    )

    assert isinstance(adapter, OpenClawLocalAdapter)


def test_openclaw_local_runtime_writes_temp_config_and_cleanup(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = OpenClawLocalRuntime()

    session = runtime.prepare_session(
        run_id="oa-openclaw-local-anthropic-claude-sonnet-4-test1234",
        model="anthropic/claude-sonnet-4",
        workspace=workspace,
        task_domain="finance",
        mcp_url="http://127.0.0.1:8123/sse",
        timeout_seconds=45,
    )

    config = json.loads(session.config_path.read_text(encoding="utf-8"))

    assert session.command[:3] == ("openclaw", "agent", "--local")
    assert session.agent_id == "sasp-bench"
    assert session.session_id == "oa-openclaw-local-anthropic-claude-sonnet-4-test1234"
    assert session.env["OPENCLAW_CONFIG_PATH"] == str(session.config_path)
    assert session.env["OPENCLAW_STATE_DIR"] == str(session.state_dir)
    assert config["agents"]["defaults"]["workspace"] == str(workspace)
    assert config["agents"]["defaults"]["timeoutSeconds"] == 45
    assert config["agents"]["list"][0]["id"] == "sasp-bench"
    assert config["agents"]["list"][0]["workspace"] == str(workspace)
    assert config["agents"]["list"][0]["model"]["primary"] == "anthropic/claude-sonnet-4"
    assert config["mcp"]["servers"]["masp_tools"]["url"] == "http://127.0.0.1:8123/sse"
    assert config["mcp"]["servers"]["masp_tools"]["transport"] == "sse"

    root_dir = session.root_dir
    runtime.cleanup()

    assert not root_dir.exists()


def test_openclaw_local_runtime_hardcodes_openai_base_url(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = OpenClawLocalRuntime()

    session = runtime.prepare_session(
        run_id="ol-openclaw-openai-gpt-5-4-test1234",
        model="openai/gpt-5.4",
        workspace=workspace,
        task_domain="finance",
        mcp_url="http://127.0.0.1:8123/sse",
        timeout_seconds=45,
    )

    config = json.loads(session.config_path.read_text(encoding="utf-8"))

    assert config["models"]["providers"]["openai"]["baseUrl"] == "https://new.suxi.ai/v1"

    runtime.cleanup()


def test_openclaw_local_adapter_rejects_multi_agent_tasks() -> None:
    adapter = OpenClawLocalAdapter(agent_timeout=30)
    ctx = RunContext(
        run_id="oa-openclaw-local-anthropic-claude-sonnet-4-test1234",
        task=_build_task(roles=["lead", "specialist"]),
        catalog=_build_catalog(),
        model="anthropic/claude-sonnet-4",
        max_turns=5,
        bank=None,
        workspace=None,
        action_sink=ActionSink(run_id="oa-openclaw-local-anthropic-claude-sonnet-4-test1234"),
    )

    outcome = asyncio.run(adapter.run(ctx, "analyze the account"))

    assert outcome.framework == "openclaw_local"
    assert outcome.final_output == ""
    assert outcome.error is not None
    assert "single-agent" in outcome.error


def test_openclaw_local_adapter_passes_bank_source_to_mcp_server() -> None:
    adapter = OpenClawLocalAdapter(agent_timeout=30)
    ctx = RunContext(
        run_id="oa-openclaw-local-anthropic-claude-sonnet-4-test1234",
        task=_build_task(roles=["investment_analyst"]),
        catalog=_build_catalog(),
        model="anthropic/claude-sonnet-4",
        max_turns=5,
        bank=None,
        workspace=None,
        action_sink=ActionSink(run_id="oa-openclaw-local-anthropic-claude-sonnet-4-test1234"),
        bank_source="sasp",
    )

    cmd = adapter._build_mcp_server_cmd(
        ctx,
        Path("/tmp/masp_bank_test.sqlite"),
    )

    assert "--bank-source" in cmd
    assert cmd[cmd.index("--bank-source") + 1] == "sasp"
