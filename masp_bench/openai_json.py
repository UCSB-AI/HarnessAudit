"""Shared OpenAI SDK helpers for JSON-only model calls."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from importlib import import_module
from typing import Any


class OpenAIJSONError(RuntimeError):
    """Raised when the OpenAI SDK is unavailable or returns invalid JSON."""


log = logging.getLogger(__name__)
_ASYNC_OPENAI_CLIENT: Any | None = None
_ASYNC_OPENAI_CLIENT_CONFIG: tuple[str | None, ...] | None = None

_TRANSIENT_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
_TRANSIENT_ERROR_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "ConnectError",
    "ConnectionTimeout",
    "ReadTimeout",
    "Timeout",
}


def _retry_attempts() -> int:
    raw = os.getenv("MASP_OPENAI_RETRY_ATTEMPTS", "3")
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _retry_base_delay() -> float:
    raw = os.getenv("MASP_OPENAI_RETRY_BASE_DELAY", "1.0")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 1.0


def _status_code(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status

    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _is_transient_openai_error(exc: BaseException) -> bool:
    status = _status_code(exc)
    if status in _TRANSIENT_STATUS_CODES:
        return True
    return exc.__class__.__name__ in _TRANSIENT_ERROR_NAMES


def _request_error_message(exc: BaseException) -> str:
    status = _status_code(exc)
    status_part = f" status={status}" if status is not None else ""
    return f"OpenAI request failed{status_part}: {exc}"


def _retry_delay(base_delay: float, attempt: int) -> float:
    return base_delay * (2 ** max(0, attempt - 1))


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


def _close_sync_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception as exc:  # noqa: BLE001
        log.debug("Failed to close OpenAI client cleanly: %s", exc)


async def _close_async_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if not callable(close):
        close = getattr(client, "aclose", None)
    if not callable(close):
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # noqa: BLE001
        log.debug("Failed to close async OpenAI client cleanly: %s", exc)


async def _get_async_openai_client() -> Any:
    """Return a process-local async OpenAI client for JSON judge calls."""

    global _ASYNC_OPENAI_CLIENT, _ASYNC_OPENAI_CLIENT_CONFIG

    openai = import_module("openai")
    config = (
        os.getenv("OPENAI_API_KEY"),
        os.getenv("OPENAI_BASE_URL"),
        os.getenv("OPENAI_ORG_ID"),
        os.getenv("OPENAI_PROJECT"),
        str(id(asyncio.get_running_loop())),
    )
    if _ASYNC_OPENAI_CLIENT is None or _ASYNC_OPENAI_CLIENT_CONFIG != config:
        if _ASYNC_OPENAI_CLIENT is not None:
            await _close_async_client(_ASYNC_OPENAI_CLIENT)
        _ASYNC_OPENAI_CLIENT = openai.AsyncOpenAI()
        _ASYNC_OPENAI_CLIENT_CONFIG = config
    return _ASYNC_OPENAI_CLIENT


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

    attempts = _retry_attempts()
    base_delay = _retry_base_delay()
    try:
        for attempt in range(1, attempts + 1):
            try:
                response = client.responses.create(
                    model=model,
                    instructions=system_prompt,
                    input=user_prompt,
                    temperature=0,
                    max_output_tokens=max_output_tokens,
                )
                break
            except Exception as exc:  # noqa: BLE001
                if attempt < attempts and _is_transient_openai_error(exc):
                    delay = _retry_delay(base_delay, attempt)
                    log.warning(
                        "Transient OpenAI JSON request failure on attempt %d/%d; "
                        "retrying in %.1fs: %s",
                        attempt,
                        attempts,
                        delay,
                        exc,
                    )
                    if delay:
                        time.sleep(delay)
                    continue
                raise OpenAIJSONError(_request_error_message(exc)) from exc

        return _parse_json_response(response)
    finally:
        _close_sync_client(client)


async def create_json_response_async(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int = 1024,
) -> Any:
    try:
        client = await _get_async_openai_client()
    except Exception as exc:  # noqa: BLE001
        raise OpenAIJSONError(
            "OpenAI SDK is required for LLM-as-judge scoring. "
            "Install the optional oai dependencies and configure OpenAI credentials."
        ) from exc

    attempts = _retry_attempts()
    base_delay = _retry_base_delay()
    for attempt in range(1, attempts + 1):
        try:
            response = await client.responses.create(
                model=model,
                instructions=system_prompt,
                input=user_prompt,
                temperature=0,
                max_output_tokens=max_output_tokens,
            )
            break
        except Exception as exc:  # noqa: BLE001
            if attempt < attempts and _is_transient_openai_error(exc):
                delay = _retry_delay(base_delay, attempt)
                log.warning(
                    "Transient OpenAI JSON request failure on attempt %d/%d; "
                    "retrying in %.1fs: %s",
                    attempt,
                    attempts,
                    delay,
                    exc,
                )
                if delay:
                    await asyncio.sleep(delay)
                continue
            raise OpenAIJSONError(_request_error_message(exc)) from exc
    return _parse_json_response(response)
