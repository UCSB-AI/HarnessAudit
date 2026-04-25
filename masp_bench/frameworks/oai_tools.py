"""Tool factories for the OpenAI Agents SDK adapter.

Creates ``FunctionTool`` wrappers that bridge MASP ToolCatalog definitions
to the OpenAI Agents SDK, dispatching through Bank / fixture / mock and
emitting ActionSink events.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from agents import FunctionTool, Runner
from agents.tool_context import ToolContext

if TYPE_CHECKING:
    from masp_bench.schemas.task import ToolCatalog, ToolDef


# ---------------------------------------------------------------------------
# Parameter schema helpers
# ---------------------------------------------------------------------------

_TYPE_PREFIX_RE = re.compile(r"^\((int|float|str|string|bool)\)\s*")


def _json_type_from_desc(desc: str) -> str:
    """Extract JSON Schema type from ToolDef param description like '(int) Client ID'."""
    m = _TYPE_PREFIX_RE.match(desc)
    if not m:
        return "string"
    t = m.group(1)
    if t == "int":
        return "integer"
    if t == "float":
        return "number"
    if t == "bool":
        return "boolean"
    return "string"


def _build_params_schema(td: ToolDef) -> dict[str, Any]:
    """Build a strict JSON Schema from ToolDef.params."""
    properties: dict[str, Any] = {}
    for name, desc in td.params.items():
        properties[name] = {"type": _json_type_from_desc(desc), "description": desc}
    return {
        "type": "object",
        "properties": properties,
        "required": list(td.params.keys()),
        "additionalProperties": False,  # required for strict_json_schema=True
    }


# ---------------------------------------------------------------------------
# Domain tool factory
# ---------------------------------------------------------------------------

def build_domain_tools(
    catalog: ToolCatalog,
    workspace: Any = None,
) -> list[FunctionTool]:
    """Create one ``FunctionTool`` per ToolDef in the catalog.

    Each tool dispatches through the Bank→real→fixture→mock cascade and emits
    an ``ActionSink.emit_tool_call`` via the shared ``MASPContext``.
    """
    tools: list[FunctionTool] = []
    for td in catalog.tools:
        tools.append(FunctionTool(
            name=td.name,
            description=td.description or td.name,
            params_json_schema=_build_params_schema(td),
            on_invoke_tool=_make_domain_handler(td, workspace),
            strict_json_schema=True,
        ))
    return tools


def _make_domain_handler(td: ToolDef, workspace: Any):
    """Return an async handler closure for a single domain tool."""

    async def handler(ctx: ToolContext[Any], args_json: str) -> str:
        args = json.loads(args_json)
        masp = ctx.context  # MASPContext
        agent_role = masp.agent_role

        from .common import dispatch_tool

        result = dispatch_tool(
            td.name, args, bank=masp.bank, tool_def=td, workspace=workspace,
        )

        masp.action_sink.emit_tool_call(
            agent_id=agent_role,
            agent_role=agent_role,
            tool_name=td.name,
            tool_args=args,
            tool_result=result,
            raw_event={"framework": "oai", "capture_mode": "inline_dispatch"},
        )
        return result

    return handler


# ---------------------------------------------------------------------------
# Delegate-to-agent tool (hub → spoke, enables parallel execution)
# ---------------------------------------------------------------------------

def build_delegate_tool(max_spoke_turns: int = 10) -> FunctionTool:
    """Create the ``delegate_to_agent`` tool used by the hub.

    When the hub LLM proposes multiple delegate calls in a single turn
    (``parallel_tool_calls=True``), the SDK executes them concurrently.
    Each call runs ``Runner.run`` for the target spoke agent.
    """

    async def delegate_to_agent(ctx: ToolContext[Any], args_json: str) -> str:
        args = json.loads(args_json)
        target = args["agent_name"]
        task_msg = args["task"]
        masp = ctx.context  # MASPContext
        sender = masp.agent_role
        sink = masp.action_sink

        # --- recursion guard ---
        if masp.depth >= 2:
            return "[ERROR] Max delegation depth reached."

        spoke = masp.spoke_agents.get(target)
        if spoke is None:
            return f"[ERROR] Unknown agent '{target}'. Available: {list(masp.spoke_agents.keys())}"

        # --- emit hub → spoke communication ---
        sink.emit_communication(
            agent_id=sender,
            agent_role=sender,
            target_agent=target,
            target_role=target,
            content=task_msg,
            raw_event={"framework": "oai", "capture_mode": "delegation"},
        )

        # --- run spoke agent (async → concurrent when parallel_tool_calls) ---
        from .oai_adapter import MASPContext  # deferred to avoid circular import

        spoke_ctx = MASPContext(
            action_sink=sink,
            bank=masp.bank,
            workspace=masp.workspace,
            agent_role=target,
            spoke_agents=masp.spoke_agents,
            depth=masp.depth + 1,
        )
        try:
            result = await Runner.run(
                spoke, task_msg, context=spoke_ctx, max_turns=max_spoke_turns,
            )
            output = str(result.final_output) if result.final_output else ""
        except Exception as exc:
            output = f"[ERROR] {exc}"

        # --- emit spoke → hub communication ---
        sink.emit_communication(
            agent_id=target,
            agent_role=target,
            target_agent=sender,
            target_role=sender,
            content=output,
            raw_event={"framework": "oai", "capture_mode": "spoke_report"},
        )
        return output

    return FunctionTool(
        name="delegate_to_agent",
        description=(
            "Delegate a sub-task to a specialist agent by name. "
            "The agent will execute the task using domain tools and return findings. "
            "Multiple calls in one turn execute concurrently."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Name of the specialist agent to delegate to.",
                },
                "task": {
                    "type": "string",
                    "description": (
                        "Sub-task description with all necessary IDs, "
                        "parameters, and context for the specialist."
                    ),
                },
            },
            "required": ["agent_name", "task"],
            "additionalProperties": False,
        },
        on_invoke_tool=delegate_to_agent,
        strict_json_schema=True,
    )
