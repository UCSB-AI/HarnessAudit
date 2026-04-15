from __future__ import annotations

import asyncio

from masp_bench.frameworks.action_sink import ActionSink
from masp_bench.frameworks.base import (
    CommunicationInterceptionDecision,
    ToolInterceptionDecision,
)
from masp_bench.frameworks.tool_runtime import ToolRuntime
from masp_bench.schemas.task import ToolDef


class _BlockingHook:
    def __init__(self) -> None:
        self.tool_modes: list[str] = []
        self.comm_modes: list[str] = []

    def before_tool_call(
        self,
        *,
        run_id: str,
        defense_mode: str,
        agent_role: str,
        tool_name: str,
        tool_args: dict,
    ) -> ToolInterceptionDecision:
        self.tool_modes.append(defense_mode)
        return ToolInterceptionDecision(
            allow=False,
            override_result="blocked by policy",
            metadata={"policy": "tool-block"},
        )

    def before_communication(
        self,
        *,
        run_id: str,
        defense_mode: str,
        agent_role: str,
        target_role: str,
        content: str,
        kind: str,
    ) -> CommunicationInterceptionDecision:
        self.comm_modes.append(defense_mode)
        return CommunicationInterceptionDecision(
            allow=False,
            override_content="redacted",
            metadata={"policy": "comm-block"},
        )


def test_tool_runtime_applies_blocking_decision() -> None:
    hook = _BlockingHook()
    runtime = ToolRuntime(
        run_id="run-1",
        defense_mode="enforced",
        bank=None,
        workspace=None,
        defense_hook=hook,
    )
    tool_def = ToolDef(
        name="lookup_profile",
        description="lookup profile",
        params={"user_id": "user id"},
        mock_response="ok",
    )

    outcome = asyncio.run(
        runtime.invoke(
            tool_def=tool_def,
            tool_args={"user_id": "U-1"},
            caller_role="hub",
        )
    )

    assert outcome.blocked is True
    assert outcome.output == "blocked by policy"
    assert outcome.defense_meta == {"policy": "tool-block"}
    assert hook.tool_modes == ["enforced"]


def test_action_sink_applies_blocking_decision() -> None:
    hook = _BlockingHook()
    sink = ActionSink(
        run_id="run-2",
        defense_mode="declared",
        defense_hook=hook,
    )

    sink.emit_communication(
        agent_id="hub",
        agent_role="hub",
        target_agent="user",
        target_role="user",
        content="original",
        raw_event={"source": "test"},
        kind="user_output",
    )
    action = sink.actions[0]

    assert action.content == "redacted"
    assert action.raw_event["source"] == "test"
    assert action.raw_event["policy"] == "comm-block"
    assert action.raw_event["defense_blocked"] is True
    assert hook.comm_modes == ["declared"]
