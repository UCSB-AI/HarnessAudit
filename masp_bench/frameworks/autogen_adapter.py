"""AutoGen AgentChat adapter for unified AAP execution."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, create_model

from .base import MASFrameworkAdapter, RunContext, RunOutcome
from .common import (
    build_hub_orchestration_suffix,
    build_spoke_suffix,
    to_builtin,
)


class _AutogenEventCollector(logging.Handler):
    """Collect structured AutoGen logger events for raw provenance."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        try:
            payload = json.loads(message)
            if isinstance(payload, dict):
                self.events.append(payload)
                return
        except Exception:
            pass
        self.events.append({"message": message, "level": record.levelname})

    def latest(self, event_type: str | None = None) -> dict[str, Any] | None:
        if event_type is None:
            return self.events[-1] if self.events else None
        for event in reversed(self.events):
            if event.get("type") == event_type:
                return event
        return None


def _safe_model_name(name: str) -> str:
    """Normalize dynamic pydantic model names."""
    normalized = re.sub(r"[^0-9a-zA-Z_]+", "_", name)
    if not normalized:
        return "ToolArgsModel"
    if normalized[0].isdigit():
        normalized = f"m_{normalized}"
    return normalized


def _autogen_task_from_input(initial_input: str | list[dict[str, Any]]) -> str:
    """Convert benchmark input payload into AutoGen task text."""
    if isinstance(initial_input, str):
        return initial_input
    text_parts: list[str] = []
    for message in initial_input:
        content = message.get("content", [])
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "input_text":
                    text = str(item.get("text", "")).strip()
                    if text:
                        text_parts.append(text)
                elif item.get("type") == "input_image":
                    text_parts.append("[image asset attached]")
                elif item.get("type") == "input_file":
                    filename = str(item.get("filename", "attached_file"))
                    text_parts.append(f"[file asset attached: {filename}]")
    return "\n".join(text_parts).strip()


def _message_text(message: Any) -> str:
    """Extract text payload from AutoGen stream message."""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if content is not None:
        try:
            return json.dumps(to_builtin(content), ensure_ascii=False)
        except Exception:
            return str(content)
    to_text = getattr(message, "to_text", None)
    if callable(to_text):
        try:
            return str(to_text())
        except Exception:
            return str(message)
    return str(message)


def _autogen_model_info_for_alias(model: str) -> dict[str, Any] | None:
    """Return AutoGen model_info for local aliases not in autogen model table."""
    alias_prefixes = ("gpt-5.4", "gpt-5.1")
    if not any(model == prefix or model.startswith(f"{prefix}-") for prefix in alias_prefixes):
        return None
    return {
        "vision": True,
        "function_calling": True,
        "json_output": True,
        "family": "gpt-5",
        "structured_output": True,
        "multiple_system_messages": True,
    }


def _openai_client_kwargs(model: str) -> dict[str, Any]:
    """Build OpenAIChatCompletionClient kwargs with optional alias model_info."""
    kwargs: dict[str, Any] = {"model": model}
    model_info = _autogen_model_info_for_alias(model)
    if model_info is not None:
        kwargs["model_info"] = model_info
    return kwargs


class AutoGenAdapter(MASFrameworkAdapter):
    """Run tasks using AutoGen AgentChat (Swarm) with unified action capture."""

    framework_id = "autogen"

    async def run(self, ctx: RunContext, initial_input: str | list[dict[str, Any]]) -> RunOutcome:
        try:
            from autogen_agentchat.agents import AssistantAgent
            from autogen_agentchat.base import TaskResult
            from autogen_agentchat.messages import HandoffMessage
            from autogen_agentchat.teams import Swarm
            from autogen_core import CancellationToken
            from autogen_core.tools import BaseTool
            from autogen_ext.models.openai import OpenAIChatCompletionClient
        except Exception as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "AutoGen dependencies are missing. "
                "Install optional dependency group: pip install .[autogen]"
            ) from exc

        collector = _AutogenEventCollector()
        core_logger = logging.getLogger("autogen_core.events")
        chat_logger = logging.getLogger("autogen_agentchat.events")
        core_logger.addHandler(collector)
        chat_logger.addHandler(collector)

        class CatalogTool(BaseTool[BaseModel, str]):
            """Dynamic AutoGen tool wrapper bound to one benchmark agent role."""

            def __init__(self, agent_role: str, tool_def: Any) -> None:
                fields = {
                    pname: (str, Field(default=..., description=pdesc or pname))
                    for pname, pdesc in tool_def.params.items()
                }
                model_name = _safe_model_name(f"{agent_role}_{tool_def.name}_args")
                args_model = create_model(model_name, **fields)
                super().__init__(
                    args_type=args_model,
                    return_type=str,
                    name=tool_def.name,
                    description=tool_def.description or tool_def.name,
                    strict=False,
                )
                self._agent_role = agent_role
                self._tool_def = tool_def

            async def run(self, args: BaseModel, cancellation_token: Any) -> str:
                tool_args = args.model_dump()
                outcome = await ctx.tool_runtime.invoke(
                    tool_def=self._tool_def,
                    tool_args=tool_args,
                    caller_role=self._agent_role,
                )
                raw_event = {
                    "framework": "autogen",
                    "tool_source": "catalog_dispatch",
                    "agent_runtime": "assistant_agent",
                }
                raw_event.update(outcome.defense_meta)
                tool_event = collector.latest(event_type="ToolCall")
                if tool_event is not None:
                    raw_event["autogen_tool_event"] = tool_event
                if outcome.blocked:
                    raw_event["defense_blocked"] = True
                ctx.action_sink.emit_tool_call(
                    agent_id=self._agent_role,
                    agent_role=self._agent_role,
                    tool_name=self._tool_def.name,
                    tool_args=tool_args,
                    tool_result=outcome.output,
                    raw_event=raw_event,
                )
                return outcome.output

        model_client = OpenAIChatCompletionClient(**_openai_client_kwargs(ctx.model))
        final_text = ""
        hub_role = ctx.task.agents[0].role

        try:
            participants: list[Any] = []
            for idx, agent_cfg in enumerate(ctx.task.agents):
                base_prompt = agent_cfg.system_prompt or agent_cfg.description
                suffix = (
                    build_hub_orchestration_suffix(hub_role)
                    if idx == 0
                    else build_spoke_suffix(hub_role)
                )
                handoffs = [a.role for a in ctx.task.agents if a.role != agent_cfg.role]
                tools = [CatalogTool(agent_cfg.role, tool_def) for tool_def in ctx.catalog.tools]
                participants.append(
                    AssistantAgent(
                        name=agent_cfg.role,
                        model_client=model_client,
                        tools=tools,
                        handoffs=handoffs,
                        system_message=base_prompt + suffix,
                        reflect_on_tool_use=False,
                        max_tool_iterations=1,
                    )
                )

            team = Swarm(
                participants=participants,
                max_turns=ctx.max_turns,
                emit_team_events=True,
            )
            task_input = _autogen_task_from_input(initial_input)
            cancellation_token = CancellationToken()

            async for message in team.run_stream(
                task=task_input,
                cancellation_token=cancellation_token,
            ):
                if isinstance(message, HandoffMessage):
                    handoff_context_raw: list[Any] = []
                    for item in message.context:
                        if hasattr(item, "model_dump"):
                            handoff_context_raw.append(item.model_dump(mode="json"))
                        else:
                            handoff_context_raw.append(to_builtin(item))
                    ctx.action_sink.emit_handoff(
                        from_agent_id=message.source,
                        from_role=message.source,
                        to_agent_id=message.target,
                        to_role=message.target,
                        handoff_note=message.content,
                        handoff_context_raw=handoff_context_raw,
                        raw_event={
                            "communication_kind": "handoff",
                            "capture_mode": "autogen_handoff_message",
                        },
                    )
                    continue

                if isinstance(message, TaskResult):
                    final_messages = [
                        m for m in message.messages if not isinstance(m, HandoffMessage)
                    ]
                    final_msg = final_messages[-1] if final_messages else None
                    if final_msg is not None:
                        final_text = _message_text(final_msg)
                        last_role = getattr(final_msg, "source", hub_role)
                    else:
                        final_text = message.stop_reason or ""
                        last_role = hub_role
                    ctx.action_sink.emit_communication(
                        agent_id=last_role,
                        agent_role=last_role,
                        target_agent="user",
                        target_role="user",
                        content=final_text,
                        raw_event={
                            "framework": "autogen",
                            "communication_kind": "user_output",
                            "capture_mode": "task_result_final_message",
                        },
                        kind="user_output",
                    )
                    break

            return RunOutcome(framework=self.framework_id, final_output=final_text)
        except Exception as exc:
            return RunOutcome(framework=self.framework_id, final_output=final_text, error=str(exc))
        finally:
            core_logger.removeHandler(collector)
            chat_logger.removeHandler(collector)
            try:
                await model_client.close()
            except Exception:
                pass
