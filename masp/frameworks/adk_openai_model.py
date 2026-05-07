"""OpenAI-compatible model bridge for Google ADK.

This avoids ADK's LiteLLM optional dependency so MASP can keep the same
``openai>=2.26`` environment required by the OpenAI Agents SDK harness.
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types


class ADKOpenAIModel(BaseLlm):
    """Minimal ADK ``BaseLlm`` backed by OpenAI Chat Completions."""

    api_key: str | None = None
    base_url: str | None = None

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        if stream:
            yield LlmResponse(
                error_code="UNSUPPORTED_STREAMING",
                error_message="ADKOpenAIModel does not support streaming.",
            )
            return

        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=self.api_key or os.environ.get("OPENAI_API_KEY"),
                base_url=self.base_url or os.environ.get("OPENAI_BASE_URL") or None,
                timeout=float(os.environ.get("MASP_ADK_OPENAI_TIMEOUT", "60")),
            )
            kwargs: dict[str, Any] = {
                "model": llm_request.model or self.model,
                "messages": _messages_from_request(llm_request),
            }
            tools = _tools_from_request(llm_request)
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            response = await client.chat.completions.create(**kwargs)
            message = response.choices[0].message
            parts: list[types.Part] = []
            if message.content:
                parts.append(types.Part.from_text(text=str(message.content)))
            for tool_call in message.tool_calls or []:
                try:
                    args = json.loads(tool_call.function.arguments or "{}")
                except Exception:
                    args = {}
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            id=tool_call.id,
                            name=tool_call.function.name,
                            args=args,
                        )
                    )
                )
            if not parts:
                parts.append(types.Part.from_text(text=""))
            yield LlmResponse(content=types.Content(role="model", parts=parts))
        except Exception as exc:
            yield LlmResponse(
                error_code=type(exc).__name__,
                error_message=str(exc),
            )


def _messages_from_request(llm_request: LlmRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system_text = _system_instruction_text(llm_request.config.system_instruction)
    if system_text:
        messages.append({"role": "system", "content": system_text})

    for content in llm_request.contents:
        parts = list(content.parts or [])
        text = "\n".join(part.text for part in parts if part.text)
        function_calls = [part.function_call for part in parts if part.function_call]
        function_responses = [
            part.function_response for part in parts if part.function_response
        ]

        if function_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [
                        {
                            "id": call.id or f"call_{idx}",
                            "type": "function",
                            "function": {
                                "name": call.name or "",
                                "arguments": json.dumps(call.args or {}),
                            },
                        }
                        for idx, call in enumerate(function_calls, start=1)
                    ],
                }
            )
        elif text:
            role = "assistant" if content.role == "model" else "user"
            messages.append({"role": role, "content": text})

        for response in function_responses:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": response.id or f"call_{response.name}",
                    "content": json.dumps(response.response or {}, default=str),
                }
            )

    if not any(message["role"] == "user" for message in messages):
        messages.append({"role": "user", "content": "Continue."})
    return messages


def _system_instruction_text(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, types.Content):
        return "\n".join(part.text for part in value.parts or [] if part.text)
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if item)
    return str(value)


def _tools_from_request(llm_request: LlmRequest) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for tool in llm_request.config.tools or []:
        for declaration in tool.function_declarations or []:
            schema = declaration.parameters_json_schema
            if schema is None and declaration.parameters is not None:
                schema = declaration.parameters.model_dump(
                    exclude_none=True,
                    by_alias=True,
                )
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": declaration.name,
                        "description": declaration.description or declaration.name,
                        "parameters": schema or {"type": "object", "properties": {}},
                    },
                }
            )
    return tools
