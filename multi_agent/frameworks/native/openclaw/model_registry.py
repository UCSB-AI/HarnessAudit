"""Model alias resolution for multi_agent-managed OpenClaw runs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class OpenClawModelResolution:
    requested_model: str
    primary_ref: str
    provider_id: str
    model_id: str
    provider_config: dict[str, Any] | None = None


OPENROUTER_MODELS: tuple[dict[str, Any], ...] = (
    {
        "id": "anthropic/claude-sonnet-4.6",
        "name": "Anthropic: Claude Sonnet 4.6",
        "input": ["text", "image"],
        "contextWindow": 1000000,
    },
    {
        "id": "anthropic/claude-opus-4.6",
        "name": "Anthropic: Claude Opus 4.6",
        "input": ["text", "image"],
        "contextWindow": 1000000,
    },
    {
        "id": "openai/gpt-5.4",
        "name": "OpenAI: GPT-5.4",
        "input": ["text", "image"],
        "contextWindow": 1000000,
    },
    {
        "id": "google/gemini-3.1-pro-preview",
        "name": "Google: Gemini 3.1 Pro Preview",
        "reasoning": True,
        "input": ["text", "image"],
        "contextWindow": 1000000,
    },
    {
        "id": "qwen/qwen3.5-plus-20260420",
        "name": "Qwen: Qwen3.5 Plus 2026-04-20",
        "reasoning": True,
        "input": ["text", "image"],
        "contextWindow": 1000000,
    },
    {
        "id": "moonshotai/kimi-k2.6",
        "name": "MoonshotAI: Kimi K2.6",
        "reasoning": True,
        "input": ["text", "image"],
        "contextWindow": 256000,
    },
    {
        "id": "z-ai/glm-5v-turbo",
        "name": "Z.ai: GLM 5V Turbo",
        "reasoning": True,
        "input": ["text", "image"],
        "contextWindow": 202752,
    },
)

OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_OPENROUTER_MODEL_BY_ID = {str(model["id"]): model for model in OPENROUTER_MODELS}

_ALIASES: dict[str, tuple[str, str]] = {
    "claude-sonnet-4-6": ("openrouter", "anthropic/claude-sonnet-4.6"),
    "claude-opus-4-6": ("openrouter", "anthropic/claude-opus-4.6"),
    "openrouter-claude-sonnet-4-6": ("openrouter", "anthropic/claude-sonnet-4.6"),
    "openrouter-claude-opus-4-6": ("openrouter", "anthropic/claude-opus-4.6"),
    "anthropic/claude-sonnet-4.6": ("openrouter", "anthropic/claude-sonnet-4.6"),
    "anthropic/claude-opus-4.6": ("openrouter", "anthropic/claude-opus-4.6"),
    "gpt-5.4": ("openrouter", "openai/gpt-5.4"),
    "openai/gpt-5.4": ("openrouter", "openai/gpt-5.4"),
    "gemini-3.1-pro": ("openrouter", "google/gemini-3.1-pro-preview"),
    "gemini-3.1-pro-preview": ("openrouter", "google/gemini-3.1-pro-preview"),
    "google/gemini-3.1-pro-preview": ("openrouter", "google/gemini-3.1-pro-preview"),
    "kimi-k2.6": ("openrouter", "moonshotai/kimi-k2.6"),
    "moonshotai/kimi-k2.6": ("openrouter", "moonshotai/kimi-k2.6"),
    "glm-5v-turbo": ("openrouter", "z-ai/glm-5v-turbo"),
    "z-ai/glm-5v-turbo": ("openrouter", "z-ai/glm-5v-turbo"),
    "qwen3.5-plus": ("openrouter", "qwen/qwen3.5-plus-20260420"),
    "qwen/qwen3.5-plus-20260420": ("openrouter", "qwen/qwen3.5-plus-20260420"),
}


def _env_value(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key, "")).strip()


def _normalized_base_url(value: str) -> str:
    return value.strip().rstrip("/")


def _openrouter_models_for_provider() -> list[dict[str, Any]]:
    return [dict(model) for model in OPENROUTER_MODELS]


def _resolve_provider_and_model(requested_model: str) -> tuple[str, str]:
    model = requested_model.strip()
    if not model:
        raise ValueError("OpenClaw model must not be empty")

    aliased = _ALIASES.get(model)
    if aliased is not None:
        return aliased

    if "/" in model:
        provider_id, model_id = (part.strip() for part in model.split("/", 1))
        if not provider_id or not model_id:
            raise ValueError(f"Invalid OpenClaw model ref: {requested_model!r}")
        if provider_id == "openrouter":
            return provider_id, model_id
        supported = ", ".join(sorted(_ALIASES))
        raise RuntimeError(
            "OpenClaw models must use OpenRouter. Use a supported alias "
            f"or an openrouter/<model-id> ref. Supported aliases: {supported}"
        )

    supported = ", ".join(sorted(_ALIASES))
    raise RuntimeError(
        f"Unsupported OpenClaw model {requested_model!r}. "
        f"Supported aliases: {supported}"
    )


def resolve_openclaw_model(
    requested_model: str,
    *,
    env: Mapping[str, str] | None = None,
) -> OpenClawModelResolution:
    env_map = env or os.environ
    provider_id, model_id = _resolve_provider_and_model(requested_model)

    if model_id not in _OPENROUTER_MODEL_BY_ID:
        supported = ", ".join(sorted(_OPENROUTER_MODEL_BY_ID))
        raise RuntimeError(
            "Unsupported OpenClaw OpenRouter model "
            f"{provider_id}/{model_id}. Supported OpenRouter models: {supported}"
        )

    api_key = _env_value(env_map, "OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is required for OpenClaw model "
            f"{provider_id}/{model_id}"
        )
    base_url = _normalized_base_url(
        _env_value(env_map, "OPENROUTER_BASE_URL")
        or OPENROUTER_DEFAULT_BASE_URL
    )
    authorization_header = f"Bearer {api_key}"
    provider_config = {
        "baseUrl": base_url,
        "apiKey": api_key,
        "auth": "api-key",
        "authHeader": True,
        "api": "openai-completions",
        "headers": {
            "Authorization": authorization_header,
        },
        "request": {
            "headers": {
                "Authorization": authorization_header,
            },
        },
        "models": _openrouter_models_for_provider(),
    }

    return OpenClawModelResolution(
        requested_model=requested_model,
        primary_ref=f"{provider_id}/{model_id}",
        provider_id=provider_id,
        model_id=model_id,
        provider_config=provider_config,
    )


def apply_openclaw_model_config(
    config: dict[str, Any],
    requested_model: str,
    *,
    env: Mapping[str, str] | None = None,
) -> OpenClawModelResolution:
    models_cfg = config.get("models")
    if not isinstance(models_cfg, dict):
        models_cfg = {}
        config["models"] = models_cfg
    providers = models_cfg.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        models_cfg["providers"] = providers

    resolved = resolve_openclaw_model(
        requested_model,
        env=env,
    )

    agents_config = config.get("agents")
    if not isinstance(agents_config, dict):
        agents_config = {}
        config["agents"] = agents_config
    defaults = agents_config.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
    else:
        defaults = dict(defaults)
    defaults["model"] = {"primary": resolved.primary_ref}
    agents_config["defaults"] = defaults

    agent_list = agents_config.get("list")
    if isinstance(agent_list, list):
        rewritten_list: list[Any] = []
        for item in agent_list:
            if isinstance(item, dict):
                rewritten_item = dict(item)
                rewritten_item["model"] = {"primary": resolved.primary_ref}
                rewritten_list.append(rewritten_item)
            else:
                rewritten_list.append(item)
        agents_config["list"] = rewritten_list

    models_cfg["mode"] = "merge"
    if resolved.provider_config is not None:
        providers[resolved.provider_id] = resolved.provider_config

    return resolved
