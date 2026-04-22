"""OpenAI Agents SDK adapter for MASP_Bench.

Mode A (in-process): all agents run in a single Python process.  The hub
agent delegates to spoke agents via ``delegate_to_agent`` FunctionTool calls.
When the LLM proposes *parallel* tool calls, the SDK executes them
concurrently (asyncio), giving true wall-clock concurrency for LLM I/O.

Bank is accessed directly via ``bank.dispatch()`` — no MCP server, no
serialisation overhead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from .base import MASFrameworkAdapter, RunContext, RunOutcome


# ---------------------------------------------------------------------------
# Shared context threaded through Runner → tools → nested runs
# ---------------------------------------------------------------------------

@dataclass
class MASPContext:
    """Propagated to every tool via ``ToolContext[MASPContext].context``."""

    action_sink: Any
    """ActionSink instance for trace emission."""

    bank: Any
    """Domain Bank (or None if fixture-only domain)."""

    workspace: Any
    """SDE workspace path (or None)."""

    agent_role: str = ""
    """Current agent's role name — set per-run so tool handlers know the caller."""

    spoke_agents: dict[str, Any] = field(default_factory=dict)
    """role → Agent mapping, used by delegate_to_agent tool."""

    depth: int = 0
    """Recursion guard: bumped on each nested Runner.run inside delegate_to_agent."""


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class OAIAdapter(MASFrameworkAdapter):
    """Run MASP tasks using the OpenAI Agents SDK (in-process)."""

    framework_id: ClassVar[str] = "oai"

    def __init__(self, agent_timeout: int = 300) -> None:
        self._agent_timeout = agent_timeout

    # ------------------------------------------------------------------
    # MASFrameworkAdapter.run
    # ------------------------------------------------------------------

    async def run(
        self,
        ctx: RunContext,
        initial_input: str | list[dict[str, Any]],
    ) -> RunOutcome:
        try:
            from agents import Agent, ModelSettings, Runner
            from agents.tracing import set_tracing_disabled
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI Agents SDK not installed.  pip install openai-agents"
            ) from exc

        from .common import build_oai_hub_prompt, build_oai_spoke_prompt, extract_input_text
        from .oai_tools import build_delegate_tool, build_domain_tools

        hub_role = ctx.task.agents[0].role
        spoke_cfgs = ctx.task.agents[1:]

        # Disable SDK tracing — ActionSink hooks are the primary trace source.
        set_tracing_disabled(True)

        try:
            # 1. Build domain tools (shared across all agents)
            domain_tools = build_domain_tools(ctx.catalog, ctx.workspace)

            # 2. Resolve model string
            model = self._resolve_model(ctx.model)
            settings = ModelSettings(parallel_tool_calls=True)

            # 3. Create spoke agents (ALL domain tools, NO delegate tool)
            spoke_agents: dict[str, Agent[MASPContext]] = {}
            for agent_cfg in spoke_cfgs:
                spoke = Agent[MASPContext](
                    name=agent_cfg.role,
                    instructions=build_oai_spoke_prompt(agent_cfg, hub_role),
                    tools=list(domain_tools),
                    model=model,
                    model_settings=settings,
                )
                spoke_agents[agent_cfg.role] = spoke

            # 4. Create hub agent (domain tools + delegate tool)
            delegate_tool = build_delegate_tool(max_spoke_turns=10)
            hub = Agent[MASPContext](
                name=hub_role,
                instructions=build_oai_hub_prompt(hub_role, spoke_cfgs, ctx.task.goal),
                tools=[*domain_tools, delegate_tool],
                model=model,
                model_settings=settings,
            )

            # 5. Build context
            masp_ctx = MASPContext(
                action_sink=ctx.action_sink,
                bank=ctx.bank,
                workspace=ctx.workspace,
                agent_role=hub_role,
                spoke_agents=spoke_agents,
            )

            # 6. Run (with timeout guard)
            import asyncio

            input_text = extract_input_text(initial_input)
            result = await asyncio.wait_for(
                Runner.run(
                    hub,
                    input_text or ctx.task.goal,
                    context=masp_ctx,
                    max_turns=ctx.max_turns,
                ),
                timeout=self._agent_timeout,
            )
            final_output = str(result.final_output) if result.final_output else ""

        except Exception as exc:
            return RunOutcome(
                framework=self.framework_id,
                final_output="",
                error=str(exc),
            )

        # 7. Emit final user_output
        ctx.action_sink.emit_communication(
            agent_id=hub_role,
            agent_role=hub_role,
            target_agent="user",
            target_role="user",
            content=final_output,
            raw_event={
                "framework": "oai",
                "communication_kind": "user_output",
            },
        )
        return RunOutcome(framework=self.framework_id, final_output=final_output)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_model(model: str) -> str:
        """Map MASP model identifier to an OpenAI SDK model string.

        ``MultiProvider`` splits on the first ``/``:
        - No prefix → OpenAI directly (``"gpt-4.1"``)
        - ``litellm/`` → ``LitellmProvider`` (``"litellm/anthropic/claude-sonnet-4"``)
        """
        if any(k in model for k in ("claude", "sonnet", "opus", "haiku")):
            return f"litellm/anthropic/{model}"
        if "gemini" in model:
            return f"litellm/gemini/{model}"
        return model

