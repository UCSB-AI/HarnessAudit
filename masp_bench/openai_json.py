"""Shared OpenAI SDK helpers for JSON-only model calls."""

from __future__ import annotations

import json
from importlib import import_module
from typing import Any


class OpenAIJSONError(RuntimeError):
    """Raised when the OpenAI SDK is unavailable or returns invalid JSON."""


def _extract_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = getattr(response, "output", None)
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            content = getattr(item, "content", None)
            if content is None and isinstance(item, dict):
                content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                text = getattr(block, "text", None)
                if text is None and isinstance(block, dict):
                    text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        if parts:
            return "".join(parts)

    raise OpenAIJSONError("OpenAI response did not contain output text")


def _parse_json_response(response: Any) -> Any:
    content = _extract_output_text(response)
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise OpenAIJSONError(f"Model returned non-JSON content: {content[:500]}") from exc


def create_json_response(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int = 1024,
) -> Any:
    try:
        openai = import_module("openai")
        client = openai.OpenAI()
    except Exception as exc:  # noqa: BLE001
        raise OpenAIJSONError(
            "OpenAI SDK is required for non-dry-run generation. "
            "Install the optional oai dependencies and configure OpenAI credentials."
        ) from exc

    response = client.responses.create(
        model=model,
        instructions=system_prompt,
        input=user_prompt,
        temperature=0,
        max_output_tokens=max_output_tokens,
    )
    return _parse_json_response(response)


async def create_json_response_async(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int = 1024,
) -> Any:
    try:
        openai = import_module("openai")
        client = openai.AsyncOpenAI()
    except Exception as exc:  # noqa: BLE001
        raise OpenAIJSONError(
            "OpenAI SDK is required for LLM-as-judge scoring. "
            "Install the optional oai dependencies and configure OpenAI credentials."
        ) from exc

    response = await client.responses.create(
        model=model,
        instructions=system_prompt,
        input=user_prompt,
        temperature=0,
        max_output_tokens=max_output_tokens,
    )
    return _parse_json_response(response)
