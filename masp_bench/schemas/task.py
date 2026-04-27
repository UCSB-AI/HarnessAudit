"""Task and tool catalog schemas.

TaskConfig  — the atomic, self-contained benchmark unit (one YAML per task).
ToolCatalog — domain-level tool inventory (loaded from tools/<domain>.yaml).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from masp_bench.schemas.access_rules import AccessRule

VALID_COMPLETION_RULE_TYPES = frozenset(
    {
        "tool_called",
        "final_output_contains",
        "sql_assert",
        "pytest_passes",
        "file_contains",
        "file_not_contains",
    }
)

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

    is_resource: bool = Field(
        False,
        description=(
            "True if this tool accesses a scoped protected resource "
            "(e.g., patient record, portfolio, case file). Resource tools "
            "are bucketed separately for Layer 1 protocol scoring."
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
    agents, tool references, access rules, and completion checkpoints.

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
            "ecommerce | office | daily_life | "
            "social_interaction | legal_compliance."
        ),
    )
    category: str | None = Field(
        None,
        description=(
            "Optional task category label, typically the immediate directory name "
            "under tasks/<domain>/, e.g. investing, consultation, or litigation."
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

    # --- Completion checkpoints (Layer 3: hybrid rule + LLM grading) ---
    completion_checkpoints: list[dict[str, Any]] = Field(
        ...,
        description=(
            "Weighted checkpoints for Layer 3 task completion scoring. "
            "Each dict has: name, type (rule|llm_judge), weight, and "
            "type-specific fields. Rule checkpoints require rule_type "
            "(tool_called, sql_assert, file_contains, pytest_passes, etc.). "
            "LLM types carry a rubric for judge evaluation."
        ),
    )

    # --- Ground truth tool paths (Layer 2: operational governance) ---
    ground_truth_tool_paths: dict[str, list[list[str]]] | None = Field(
        None,
        description=(
            "Per-role valid tool-call sets for Layer 2 judge. "
            "Keys are role names, values are lists of valid tool sets."
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

    @model_validator(mode="after")
    def _validate_checkpoint_weights(self) -> TaskConfig:
        """Validate Layer 3 checkpoint format and weights."""
        if not self.completion_checkpoints:
            raise ValueError("completion_checkpoints must contain at least one checkpoint")
        weights: list[float] = []
        missing: list[Any] = []
        for i, checkpoint in enumerate(self.completion_checkpoints):
            raw_weight = checkpoint.get("weight")
            if raw_weight is None:
                missing.append(checkpoint.get("name", f"#{i}"))
            else:
                weights.append(float(raw_weight))
        if missing:
            raise ValueError(
                f"completion_checkpoints missing weight: {missing}"
            )
        total = sum(weights)
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"completion_checkpoints weights sum to {total:.4f}, expected 1.0"
            )
        for checkpoint in self.completion_checkpoints:
            checkpoint_type = checkpoint.get("type")
            name = checkpoint.get("name", "<unnamed>")
            if checkpoint_type == "rule":
                rule_type = checkpoint.get("rule_type")
                if not rule_type:
                    raise ValueError(f"completion checkpoint {name!r} is missing rule_type")
                if rule_type not in VALID_COMPLETION_RULE_TYPES:
                    raise ValueError(
                        f"completion checkpoint {name!r} has unknown rule_type "
                        f"{rule_type!r}"
                    )
            elif checkpoint_type == "llm_judge":
                if not checkpoint.get("rubric"):
                    raise ValueError(f"LLM completion checkpoint {name!r} is missing rubric")
            else:
                raise ValueError(
                    f"completion checkpoint {name!r} has unknown type {checkpoint_type!r}"
                )
        return self
