"""Google ADK tool bridge for MASP-Bench."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from google.adk.tools.base_tool import BaseTool
from google.genai import types

if TYPE_CHECKING:
    from masp_bench.schemas.task import ToolCatalog, ToolDef


_TYPE_PREFIX_RE = re.compile(r"^\((int|float|str|string|bool)\)\s*")


def _json_type_from_desc(desc: str) -> str:
    match = _TYPE_PREFIX_RE.match(desc)
    if not match:
        return "string"
    value = match.group(1)
    if value == "int":
        return "integer"
    if value == "float":
        return "number"
    if value == "bool":
        return "boolean"
    return "string"


def _build_params_schema(td: ToolDef) -> dict[str, Any]:
    properties = {
        name: {"type": _json_type_from_desc(desc), "description": desc}
        for name, desc in td.params.items()
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(td.params.keys()),
        "additionalProperties": False,
    }


class MASPADKDomainTool(BaseTool):
    """ADK BaseTool wrapper around a MASP catalog tool."""

    def __init__(
        self,
        *,
        tool_def: ToolDef,
        runtime: Any,
        agent_role: str,
        workspace: Any = None,
    ) -> None:
        super().__init__(
            name=tool_def.name,
            description=tool_def.description or tool_def.name,
        )
        self._tool_def = tool_def
        self._runtime = runtime
        self._agent_role = agent_role
        self._workspace = workspace

    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema=_build_params_schema(self._tool_def),
        )

    async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> str:
        from .common import dispatch_tool

        try:
            result = dispatch_tool(
                self.name,
                dict(args or {}),
                bank=self._runtime.bank,
                tool_def=self._tool_def,
                workspace=self._workspace,
            )
        except Exception as exc:
            result = f"[ERROR] Tool {self.name} failed: {exc}"
        result = str(result)
        self._runtime.action_sink.emit_tool_call(
            agent_id=self._agent_role,
            agent_role=self._agent_role,
            tool_name=self.name,
            tool_args=dict(args or {}),
            tool_result=result,
            raw_event={"framework": "adk", "capture_mode": "inline_dispatch"},
        )
        return result


class MASPADKDelegateTool(BaseTool):
    """Hub-only delegation tool that runs a spoke ADK agent."""

    def __init__(self, *, runtime: Any, hub_role: str, max_spoke_turns: int = 10) -> None:
        super().__init__(
            name="delegate_to_agent",
            description=(
                "Delegate a sub-task to a specialist agent by name. "
                "The specialist executes the task and returns findings."
            ),
        )
        self._runtime = runtime
        self._hub_role = hub_role
        self._max_spoke_turns = max_spoke_turns

    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "agent_name": {
                        "type": "string",
                        "description": "Name of the specialist agent to delegate to.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Sub-task description and required context.",
                    },
                },
                "required": ["agent_name", "task"],
                "additionalProperties": False,
            },
        )

    async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> str:
        target = str((args or {}).get("agent_name", "")).strip()
        task_msg = str((args or {}).get("task", "")).strip()
        if not target or not task_msg:
            return "[ERROR] delegate_to_agent requires agent_name and task."
        spoke = self._runtime.spoke_agents.get(target)
        if spoke is None:
            return (
                f"[ERROR] Unknown agent '{target}'. "
                f"Available: {list(self._runtime.spoke_agents.keys())}"
            )

        sink = self._runtime.action_sink
        sink.emit_communication(
            agent_id=self._hub_role,
            agent_role=self._hub_role,
            target_agent=target,
            target_role=target,
            content=task_msg,
            raw_event={"framework": "adk", "capture_mode": "delegation"},
        )
        try:
            output = await self._runtime.run_agent(
                spoke,
                input_text=task_msg,
                agent_role=target,
                max_turns=self._max_spoke_turns,
            )
        except Exception as exc:
            output = f"[ERROR] {exc}"

        sink.emit_communication(
            agent_id=target,
            agent_role=target,
            target_agent=self._hub_role,
            target_role=self._hub_role,
            content=output,
            raw_event={"framework": "adk", "capture_mode": "spoke_report"},
        )
        return output


def build_domain_tools(
    catalog: ToolCatalog,
    *,
    runtime: Any,
    agent_role: str,
    workspace: Any = None,
) -> list[BaseTool]:
    return [
        MASPADKDomainTool(
            tool_def=tool_def,
            runtime=runtime,
            agent_role=agent_role,
            workspace=workspace,
        )
        for tool_def in catalog.tools
    ]


def build_delegate_tool(
    *,
    runtime: Any,
    hub_role: str,
    max_spoke_turns: int = 10,
) -> BaseTool:
    return MASPADKDelegateTool(
        runtime=runtime,
        hub_role=hub_role,
        max_spoke_turns=max_spoke_turns,
    )
