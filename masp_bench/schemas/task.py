"""Task and tool catalog schemas.

TaskConfig  — the atomic, self-contained benchmark unit (one YAML per task).
ToolCatalog — domain-level tool inventory (loaded from tools/<domain>.yaml).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from masp_bench.schemas.access_rules import AccessRule

# ---------------------------------------------------------------------------
# Tool definition & catalog (loaded from tools/<domain>.yaml)
# ---------------------------------------------------------------------------


class ToolDef(BaseModel):
    """A single tool definition."""

    name: str
    description: str = ""
    params: dict[str, str] = Field(
        default_factory=dict,
        description="Parameter name → type string, e.g. {'ticker': 'str'}.",
    )
    mock_response: str = Field(
        "", description="Static mock response (fallback when no fixture).",
    )
    fixture: str = Field(
        "",
        description=(
            "Fixture JSON filename (relative to fixtures/{domain}/), "
            "e.g. 'market_data.json'. Keys are lookup values, values are "
            "pre-formatted response strings. Special keys: _header, _footer, _default."
        ),
    )
    fixture_key: str = Field(
        "",
        description=(
            "Which param name to use as the lookup key into the fixture, "
            "e.g. 'tickers' or 'client_id'. Supports comma-separated multi-lookup."
        ),
    )
    backend_type: Literal["mock", "real"] = Field(
        "mock",
        description=(
            "mock: fixture JSON lookup + static fallback (all domains). "
            "real: actual file/subprocess execution (SDE domain only)."
        ),
    )
    # Scope-aware dedup: which arg keys identify the protected object for
    # V-OT dedup. E.g. ["case_id"] means two calls to the same tool with
    # the same case_id but different secondary args are the same overreach.
    dedup_scope_keys: list[str] = Field(
        default_factory=list,
        description=(
            "Arg key(s) that identify the protected scope for V-OT dedup. "
            "If empty, V-OT dedup falls back to tool-name-only."
        ),
    )

    # Runtime-loaded fixture data (not in YAML, populated by loader)
    fixture_data: dict[str, str] | None = Field(
        None, exclude=True,
        description="Loaded fixture dict. Keys = lookup values, values = response strings.",
    )


class ToolCatalog(BaseModel):
    """Domain-level tool inventory, loaded from tools/<domain>.yaml."""

    domain: str = Field(..., description="Domain identifier, e.g. 'finance'.")
    name: str = Field(..., description="Human-readable domain name.")
    description: str = ""
    tools: list[ToolDef] = Field(
        ..., description="Complete tool inventory for this domain.",
    )


# ---------------------------------------------------------------------------
# Agent config
# ---------------------------------------------------------------------------


class ToolNecessity(BaseModel):
    """Three-tier tool classification for an agent within a task.

    Replaces the flat ``allowed_tools`` field with a graded model:

      - ``required``    — minimum safe set; no rule emitted, fully allowed.
      - ``forbidden``   — explicitly out of scope; loader emits a high-severity
                          deny rule (penalty 0.30).
      - ``unnecessary`` — mildly out of scope; loader emits a low-severity
                          deny rule (penalty 0.15). Tools NOT mentioned in any
                          tier default to this bucket.

    Tools may appear in only one tier; the loader validates non-overlap.
    """

    required: list[str] = Field(
        default_factory=list,
        description="Minimum safe set of tools — fully allowed, no deny rule.",
    )
    unnecessary: list[str] = Field(
        default_factory=list,
        description=(
            "Mildly out-of-scope tools — synthesized as low-severity deny rules. "
            "Catalog tools NOT mentioned in any tier also default to this bucket."
        ),
    )
    forbidden: list[str] = Field(
        default_factory=list,
        description=(
            "Actively forbidden tools — synthesized as high-severity deny rules. "
            "Use for high-stakes misuse like PII reads by non-PII roles or "
            "trading authority misuse by non-trading roles."
        ),
    )


class AgentConfig(BaseModel):
    """Configuration for a single agent within a task."""

    role: str = Field(..., description="Role template name, e.g. 'research_analyst'.")
    description: str = Field(
        ..., description="One-line description of this agent's responsibilities."
    )
    system_prompt: str = Field(
        "",
        description="Base system prompt for this agent.",
    )
    tool_necessity: ToolNecessity | None = Field(
        None,
        description=(
            "Three-tier tool classification (required / unnecessary / forbidden). "
            "When set, the loader uses tier-based severities for synthesized "
            "deny rules. ``allowed_tools`` is auto-derived from ``required``."
        ),
    )
    allowed_tools: list[str] = Field(
        default_factory=list,
        description=(
            "Minimum safe set of tools this agent is authorized to call. "
            "All other tools in the domain catalog remain VISIBLE to the SDK "
            "agent (tests temptation) but calling them triggers an "
            "auto-synthesized operation.tool_call deny rule. When "
            "``tool_necessity`` is provided, this field is auto-derived from "
            "the ``required`` tier."
        ),
    )

    @model_validator(mode="after")
    def _derive_allowed_from_necessity(self) -> AgentConfig:
        """If tool_necessity is set and allowed_tools wasn't explicit, derive it."""
        if self.tool_necessity is not None and not self.allowed_tools:
            self.allowed_tools = list(self.tool_necessity.required)
        return self


class TaskInputAsset(BaseModel):
    """Task-level multimodal asset attached to the initial user input."""

    asset_type: Literal["image", "pdf"] = Field(
        ...,
        description="Initial multimodal asset type. Currently image or pdf.",
    )
    path: str = Field(
        ...,
        description=(
            "Path to the task asset. Relative paths are resolved against the task "
            "YAML directory at load time."
        ),
    )
    description: str = Field(
        "",
        description=(
            "Optional short note shown to the model before the asset is attached."
        ),
    )
    detail: Literal["low", "high", "auto", "original"] = Field(
        "auto",
        description="Vision detail hint for image assets.",
    )
    resolved_path: str | None = Field(
        None,
        exclude=True,
        description="Absolute asset path resolved by the loader at runtime.",
    )


# ---------------------------------------------------------------------------
# Task config — the atomic, self-contained benchmark unit
# ---------------------------------------------------------------------------


class TaskConfig(BaseModel):
    """A single benchmark task (self-contained).

    One YAML file per task. Contains everything needed to run and evaluate:
    agents, tool references, access rules, and success checks.

    Sensitive-data leak detection uses recognizer-based pattern matching
    (see ``masp_bench.recognizer``) keyed by the ``data_class`` field in
    ``information.data_leak`` access rules.

    Tool definitions are loaded separately from tools/<domain>.yaml.
    """

    task_id: str = Field(..., description="Unique task ID, e.g. 'fin-t1', 'sde1-t3'.")
    domain: str = Field(
        ...,
        description=(
            "Domain tag: finance | software_engineering | healthcare | "
            "ecommerce | office_enterprise_management | daily_life | "
            "social_interaction | legal_compliance | real_estate | "
            "journalism_media."
        ),
    )
    family: str | None = Field(
        None,
        description=(
            "Optional task family label, e.g. clinical_encounter or "
            "clinical_workflow."
        ),
    )
    modality: Literal["text_only", "multimodal"] = Field(
        "text_only",
        description=(
            "Initial task-input modality. multimodal tasks provide image/PDF "
            "assets in input_assets."
        ),
    )
    goal: str = Field(
        ..., description="Natural language task description given to the agents.",
    )
    input_assets: list[TaskInputAsset] = Field(
        default_factory=list,
        description=(
            "Task-level initial multimodal assets attached to the first user "
            "message when modality=multimodal."
        ),
    )
    fixture: str | None = Field(
        None,
        description=(
            "Name of the real micro-app fixture directory under fixtures/, "
            "e.g. 'app-ecommerce' or 'app-admin'. When set, the runner "
            "creates an isolated workspace copy for each run and routes "
            "backend_type=real tools through actual file I/O."
        ),
    )

    # --- Agents ---
    agents: list[AgentConfig] = Field(
        ..., description="Agent configurations for this task.",
    )

    # --- Access rules (what's forbidden) ---
    access_rules: list[AccessRule] = Field(
        default_factory=list,
        description="Access control rules. Matching an action = violation.",
    )

    # --- Success checks (for TSR computation) ---
    success_checks: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Lightweight checks to determine task completion. "
            "Each dict has a 'type' key and type-specific fields. "
            "Examples: {'type': 'tool_called', 'tool_name': 'get_market_data', 'min_calls': 1}, "
            "{'type': 'final_output_contains', 'keywords': ['AAPL', 'recommendation']}."
        ),
    )

    # --- Metadata (optional, for analysis) ---
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_modality_assets(self) -> TaskConfig:
        """Ensure multimodal tasks declare at least one initial asset."""
        if self.modality == "multimodal" and not self.input_assets:
            raise ValueError("multimodal tasks must declare at least one input_asset")
        return self
