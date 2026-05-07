"""LLM client wrapper for task generation pipeline.

Uses the shared OpenAI JSON helper from masp.openai_json.
Adds retry logic, rate limiting, and structured JSON output parsing.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from masp.openai_json import OpenAIJSONError, create_json_response

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5.4-xhigh"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 5.0  # seconds


def generate_json(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str = DEFAULT_MODEL,
    max_output_tokens: int = 4096,
    retries: int = MAX_RETRIES,
    dry_run: bool = False,
) -> Any:
    """Call gpt-5.4-xhigh and parse JSON response with retries.

    When *dry_run* is True, returns an empty dict without calling the API.
    """
    if dry_run:
        log.info("[dry-run] Skipping LLM call (%d system + %d user chars)",
                 len(system_prompt), len(user_prompt))
        return {}

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            result = create_json_response(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_output_tokens=max_output_tokens,
            )
            return result
        except OpenAIJSONError as exc:
            last_error = exc
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            log.warning(
                "LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt, retries, exc, delay,
            )
            time.sleep(delay)
        except Exception as exc:
            last_error = exc
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            log.warning(
                "LLM call error (attempt %d/%d): %s — retrying in %.1fs",
                attempt, retries, exc, delay,
            )
            time.sleep(delay)

    raise OpenAIJSONError(
        f"LLM call failed after {retries} attempts: {last_error}"
    ) from last_error


def generate_json_list(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str = DEFAULT_MODEL,
    max_output_tokens: int = 16384,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Call LLM expecting a JSON array response. Returns list of dicts."""
    result = generate_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model,
        max_output_tokens=max_output_tokens,
        dry_run=dry_run,
    )
    if dry_run:
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and any(
        isinstance(v, list) for v in result.values()
    ):
        # Unwrap {"tasks": [...]} or {"scenarios": [...]} etc.
        for v in result.values():
            if isinstance(v, list):
                return v
    raise OpenAIJSONError(
        f"Expected JSON array, got {type(result).__name__}: {json.dumps(result)[:200]}"
    )
