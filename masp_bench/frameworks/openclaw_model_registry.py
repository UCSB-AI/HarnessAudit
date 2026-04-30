"""Model alias resolution for MASP-managed OpenClaw runs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

LINGXIMOYU_DEFAULT_BASE_URL = "https://api.lingximoyu.com/v1"
OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
OPENCLAW_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


@dataclass(frozen=True)
class OpenClawModelResolution:
    requested_model: str
    primary_ref: str
    provider_id: str
    model_id: str
    provider_config: dict[str, Any] | None = None


LINGXIMOYU_MODELS: tuple[dict[str, Any], ...] = (
    {
        "id": "gemini-3.1-pro-preview",
        "name": "Gemini 3.1 Pro Preview",
        "input": ["text", "image"],
    },
    {
        "id": "qwen3.5-plus",
        "name": "Qwen 3.5 Plus",
        "input": ["text", "image"],
    },
)

OPENROUTER_MODELS: tuple[dict[str, Any], ...] = (
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

_LINGXIMOYU_MODEL_BY_ID = {str(model["id"]): model for model in LINGXIMOYU_MODELS}
_OPENROUTER_MODEL_BY_ID = {str(model["id"]): model for model in OPENROUTER_MODELS}
_LINGXIMOYU_BACKED_PROVIDERS = {"lingximoyu"}
_OPENROUTER_BACKED_PROVIDERS = {"openrouter"}

_ALIASES: dict[str, tuple[str, str]] = {
    "claude-sonnet-4-6": ("anthropic", "claude-sonnet-4-6"),
    "claude-opus-4-6": ("anthropic", "claude-opus-4-6"),
    "gemini-3.1-pro": ("lingximoyu", "gemini-3.1-pro-preview"),
    "gemini-3.1-pro-preview": ("lingximoyu", "gemini-3.1-pro-preview"),
    "kimi-k2.6": ("openrouter", "moonshotai/kimi-k2.6"),
    "moonshotai/kimi-k2.6": ("openrouter", "moonshotai/kimi-k2.6"),
    "glm-5v-turbo": ("openrouter", "z-ai/glm-5v-turbo"),
    "z-ai/glm-5v-turbo": ("openrouter", "z-ai/glm-5v-turbo"),
    "qwen3.5-plus": ("lingximoyu", "qwen3.5-plus"),
}


def _env_value(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key, "")).strip()


def _normalized_base_url(value: str, default: str) -> str:
    return value.strip().rstrip("/") or default


def _copy_config(value: dict[str, Any] | None) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _lingximoyu_models_for_provider(
    provider_id: str,
    model_id: str,
) -> list[dict[str, Any]]:
    if provider_id == "lingximoyu":
        return [dict(model) for model in LINGXIMOYU_MODELS]
    model = _LINGXIMOYU_MODEL_BY_ID.get(model_id)
    if model is None:
        return [{"id": model_id, "name": model_id, "input": ["text"]}]
    return [dict(model)]


def _openrouter_models_for_provider(model_id: str) -> list[dict[str, Any]]:
    model = _OPENROUTER_MODEL_BY_ID.get(model_id)
    if model is None:
        return [{"id": model_id, "name": model_id, "input": ["text"]}]
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
        return provider_id, model_id

    return ("openai", model)


def _uses_lingximoyu_credentials(provider_id: str, model_id: str) -> bool:
    return provider_id in _LINGXIMOYU_BACKED_PROVIDERS


def _uses_openrouter_credentials(provider_id: str, model_id: str) -> bool:
    return provider_id in _OPENROUTER_BACKED_PROVIDERS


def resolve_openclaw_model(
    requested_model: str,
    *,
    env: Mapping[str, str] | None = None,
    existing_provider_config: dict[str, Any] | None = None,
    openai_base_url_fallback: str = OPENCLAW_DEFAULT_OPENAI_BASE_URL,
    include_openai_base_url_env: bool = False,
) -> OpenClawModelResolution:
    env_map = env or os.environ
    provider_id, model_id = _resolve_provider_and_model(requested_model)
    provider_config: dict[str, Any] | None = None

    if _uses_lingximoyu_credentials(provider_id, model_id):
        if provider_id == "lingximoyu" and model_id not in _LINGXIMOYU_MODEL_BY_ID:
            supported = ", ".join(sorted(_LINGXIMOYU_MODEL_BY_ID))
            raise RuntimeError(
                "Unsupported Lingxi OpenClaw model "
                f"{provider_id}/{model_id}. Supported Lingxi models: {supported}"
            )
        api_key = _env_value(env_map, "LINGXIMOYU_API_KEY")
        if not api_key:
            raise RuntimeError(
                "LINGXIMOYU_API_KEY is required for OpenClaw model "
                f"{provider_id}/{model_id}"
            )
        base_url = _normalized_base_url(
            _env_value(env_map, "LINGXIMOYU_BASE_URL"),
            LINGXIMOYU_DEFAULT_BASE_URL,
        )
        provider_config = {
            "baseUrl": base_url,
            "apiKey": api_key,
            "api": "openai-completions",
            "models": _lingximoyu_models_for_provider(provider_id, model_id),
        }
    elif _uses_openrouter_credentials(provider_id, model_id):
        if provider_id == "openrouter" and model_id not in _OPENROUTER_MODEL_BY_ID:
            supported = ", ".join(sorted(_OPENROUTER_MODEL_BY_ID))
            raise RuntimeError(
                "Unsupported OpenRouter OpenClaw model "
                f"{provider_id}/{model_id}. Supported OpenRouter models: {supported}"
            )
        api_key = _env_value(env_map, "OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is required for OpenClaw model "
                f"{provider_id}/{model_id}"
            )
        base_url = _normalized_base_url(
            _env_value(env_map, "OPENROUTER_BASE_URL"),
            OPENROUTER_DEFAULT_BASE_URL,
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
            "models": _openrouter_models_for_provider(model_id),
        }
    elif provider_id == "openai":
        existing = _copy_config(existing_provider_config)
        base_url = (
            _env_value(env_map, "OPENCLAW_OPENAI_BASE_URL")
            or str(existing.get("baseUrl", "")).strip()
            or (
                _env_value(env_map, "OPENAI_BASE_URL")
                if include_openai_base_url_env
                else ""
            )
            or openai_base_url_fallback
        )
        provider_config = {
            **existing,
            "baseUrl": base_url,
            "models": [{"id": model_id, "name": model_id}],
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
    openai_base_url_fallback: str = OPENCLAW_DEFAULT_OPENAI_BASE_URL,
    include_openai_base_url_env: bool = False,
) -> OpenClawModelResolution:
    models_cfg = config.get("models")
    if not isinstance(models_cfg, dict):
        models_cfg = {}
        config["models"] = models_cfg
    providers = models_cfg.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        models_cfg["providers"] = providers

    provider_id, _ = _resolve_provider_and_model(requested_model)
    existing_provider = providers.get(provider_id)
    existing_provider_config = (
        existing_provider if isinstance(existing_provider, dict) else None
    )
    resolved = resolve_openclaw_model(
        requested_model,
        env=env,
        existing_provider_config=existing_provider_config,
        openai_base_url_fallback=openai_base_url_fallback,
        include_openai_base_url_env=include_openai_base_url_env,
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
