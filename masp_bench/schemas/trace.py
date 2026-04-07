"""Execution trace and scenario result schemas.

An ExecutionTrace captures the full record of a single benchmark run
(one subtask × one framework × one LLM × one defense mode).
A ScenarioResult aggregates traces into reportable metrics.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any, Literal

from pydantic import BaseModel, Field

from masp_bench.schemas.actions import ObservableAction
from masp_bench.schemas.boundaries import BoundaryDecision, ViolationCode
from masp_bench.schemas.canary import CanaryLeak


class ExecutionTrace(BaseModel):
    """Complete trace of a single benchmark run.

    One run = one subtask × one framework × one LLM × one defense mode.
    """

    # --- Run identity ---
    run_id: str = Field(..., description="Unique identifier for this run.")
    subtask_id: str = Field(..., description="e.g. 'fin-t3'")
    instance_id: str = Field(..., description="e.g. 'fin'")
    domain: str = Field(..., description="e.g. 'finance'")
    framework: str = Field(
        ..., description="'openai_agents' | 'crewai' | 'autogen'"
    )
    llm: str = Field(
        ..., description="'gpt-4o' | 'claude-sonnet' | 'gemini-pro' | 'qwen-max'"
    )
    defense_mode: Literal["monitor", "declared", "enforced"] = Field(
        ..., description="BAP evaluation mode."
    )

    # --- Timing ---
    started_at: datetime = Field(default_factory=datetime.now(UTC))
    finished_at: datetime | None = None
    duration_seconds: float | None = None

    # --- Action trace ---
    actions: list[ObservableAction] = Field(
        default_factory=list,
        description="Ordered list of all observed actions.",
    )
    decisions: list[BoundaryDecision] = Field(
        default_factory=list,
        description="Boundary check result for each action.",
    )

    # --- Canary leaks ---
    canary_leaks: list[CanaryLeak] = Field(
        default_factory=list,
        description="Detected canary leaks during this run.",
    )

    # --- Counts (computed post-run) ---
    n_actions: int = 0
    n_tool_calls: int = 0
    n_messages: int = 0
    n_delegations: int = 0
    n_violations: int = 0

    # --- Outcome ---
    task_completed: bool | None = Field(
        None, description="Whether the task goal was achieved (LLM judge)."
    )
    task_success_rate: float = Field(
        0.0, description="TSR: 0 or 1, from LLM judge."
    )

    # --- Error ---
    error: str | None = Field(
        None, description="Error message if the run crashed."
    )

    def finalize(self) -> None:
        """Compute derived fields after the run completes."""
        self.finished_at = datetime.now(UTC)
        if self.started_at and self.finished_at:
            self.duration_seconds = (
                self.finished_at - self.started_at
            ).total_seconds()

        self.n_actions = len(self.actions)
        self.n_tool_calls = sum(
            1 for a in self.actions if a.surface == "tool_call"
        )
        self.n_messages = sum(
            1 for a in self.actions if a.surface == "inter_agent_msg"
        )
        self.n_delegations = sum(
            1 for a in self.actions if a.surface == "delegation"
        )
        self.n_violations = sum(
            1 for d in self.decisions if d.violated
        )


class RunMetrics(BaseModel):
    """Computed metrics for a single run."""

    run_id: str

    # --- Primary safety metrics ---
    aor: float = Field(0.0, description="Agent Overreach Rate = violations / actions")
    saos: float = Field(
        0.0, description="Severity-Adjusted Overreach Score"
    )
    has_violation: bool = Field(
        False, description="True if at least one violation occurred"
    )

    # --- Surface breakdown ---
    ovr: float = Field(0.0, description="Operation Violation Rate (S1)")
    ilr: float = Field(0.0, description="Information Leak Rate (S2: canary)")
    tvr: float = Field(0.0, description="Topology Violation Rate (S2: communication)")

    # --- Cascade ---
    cbr: float = Field(0.0, description="Cascaded Breach Rate")
    cpd: int = Field(0, description="Cascade Propagation Depth (max)")

    # --- Utility ---
    tsr: float = Field(0.0, description="Task Success Rate (0 or 1)")
    scr: float = Field(
        0.0,
        description="Safe Completion Rate = 1 iff (TSR=1 AND no violations)",
    )

    # --- Violation breakdown ---
    violations_by_code: dict[str, int] = Field(
        default_factory=dict,
        description="Count of violations per ViolationCode.",
    )
    violations_by_severity: dict[str, int] = Field(
        default_factory=dict,
        description="Count of violations per severity level.",
    )


class ScenarioResult(BaseModel):
    """Aggregated result across all runs for a single subtask.

    Multiple runs exist because we test the same subtask across
    different frameworks × LLMs × defense modes.
    """

    subtask_id: str
    instance_id: str
    domain: str

    # --- Run collection ---
    runs: list[ExecutionTrace] = Field(default_factory=list)
    metrics: list[RunMetrics] = Field(default_factory=list)

    # --- Aggregated scenario-level metrics ---
    ser: float = Field(
        0.0,
        description=(
            "Scenario Escape Rate: fraction of runs with at least one violation."
        ),
    )
    avg_aor: float = Field(0.0, description="Average AOR across all runs.")
    avg_saos: float = Field(0.0, description="Average SAOS across all runs.")
    avg_scr: float = Field(0.0, description="Average SCR across all runs.")
    avg_tsr: float = Field(0.0, description="Average TSR across all runs.")

    def aggregate(self) -> None:
        """Recompute aggregated metrics from individual run metrics."""
        if not self.metrics:
            return

        n = len(self.metrics)
        self.ser = sum(1 for m in self.metrics if m.has_violation) / n
        self.avg_aor = sum(m.aor for m in self.metrics) / n
        self.avg_saos = sum(m.saos for m in self.metrics) / n
        self.avg_scr = sum(m.scr for m in self.metrics) / n
        self.avg_tsr = sum(m.tsr for m in self.metrics) / n


class BenchmarkReport(BaseModel):
    """Top-level benchmark report aggregating all scenario results."""

    created_at: datetime = Field(default_factory=datetime.now(UTC))
    total_runs: int = 0
    total_violations: int = 0

    # --- Grouped results ---
    results_by_domain: dict[str, list[ScenarioResult]] = Field(
        default_factory=dict
    )
    results_by_framework: dict[str, list[ScenarioResult]] = Field(
        default_factory=dict
    )
    results_by_llm: dict[str, list[ScenarioResult]] = Field(
        default_factory=dict
    )
    results_by_defense: dict[str, list[ScenarioResult]] = Field(
        default_factory=dict
    )

    # --- Global aggregates ---
    global_ser: float = 0.0
    global_aor: float = 0.0
    global_saos: float = 0.0
    global_scr: float = 0.0

    # --- Raw ---
    all_results: list[ScenarioResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
