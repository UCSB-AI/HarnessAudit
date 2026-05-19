"""HarnessAudit CLI — run tasks and evaluate access control violations."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from multi_agent.checker import check_trace, compute_metrics
from multi_agent.completion_judge import COMPLETION_SCORE_MODE
from multi_agent.frameworks.clawteam.backbone import canonicalize_clawteam_artifacts
from multi_agent.loader import load_task_with_tools, resolve_task_path
from multi_agent.operational_judge import evaluate_operational_governance
from multi_agent.perturbation import (
    PerturbationConfig,
    default_index_path,
    load_variant_by_id,
    source_task_sha256,
)
from multi_agent.perturbation_eval import PerturbationEvaluation, evaluate_perturbation
from multi_agent.runner import (
    BenchmarkRunner,
    framework_run_id_slug,
    next_run_id,
)
from multi_agent.trace import AccessDecisionEvent, TraceEnd, TraceWriter
from multi_agent.workspace import WorkspaceManager


def _model_dir_name(model: str) -> str:
    """Return a filesystem-safe model directory name.

    Keep the model argument readable, but prevent provider-prefixed names such
    as ``anthropic/claude-sonnet-4.6`` from becoming unintended nested paths.
    """
    model_name = (model or "unknown-model").strip() or "unknown-model"
    return model_name.replace("/", "__").replace(os.sep, "__")


def _artifact_scoped_dir(base_dir: Path, harness: str, model: str) -> Path:
    """Store run artifacts under harness/model child directories.

    Callers may pass the artifact root (``results``), a harness-scoped
    directory (``results/openclaw``), or a fully scoped directory
    (``results/openclaw/gpt-5.4``); avoid appending path components twice.
    """
    model_dir = _model_dir_name(model)
    if base_dir.name == model_dir and base_dir.parent.name == harness:
        return base_dir
    if base_dir.name == model_dir:
        return base_dir
    if base_dir.name == harness:
        return base_dir / model_dir
    return base_dir / harness / model_dir


def _effective_harness(args: argparse.Namespace) -> str:
    """Return the artifact/run harness label implied by the CLI args."""
    if args.harness:
        return args.harness
    if args.framework == "oai":
        return "oai"
    if args.framework == "adk":
        return "adk"
    return "claude"


def _artifact_prefix(task_id: str, perturbation: PerturbationConfig | None) -> str:
    if perturbation is None:
        return task_id
    return f"{task_id}_{perturbation.variant_id}"


def _perturbation_trace_payload(
    perturbation: PerturbationConfig | None,
    perturbation_result: PerturbationEvaluation | None,
) -> dict[str, Any] | None:
    if perturbation is None:
        return None
    metadata = perturbation.trace_metadata()
    if perturbation_result is None:
        return {
            **metadata,
            "delivered": None,
            "stable": None,
            "stability_score": None,
        }
    return {**metadata, **perturbation_result.as_dict()}


def _load_perturbation_config(
    args: argparse.Namespace,
    *,
    task_id: str,
    domain: str,
    task_path: Path,
) -> PerturbationConfig | None:
    variant_id = getattr(args, "perturbation_id", None)
    if not variant_id:
        return None
    if args.framework != "clawteam":
        raise ValueError("Perturbations are only supported for --framework clawteam")

    index_path = Path(args.perturbation_index) if args.perturbation_index else default_index_path()
    variant, row = load_variant_by_id(variant_id, index_path=index_path)
    if variant.task_id != task_id or variant.domain != domain:
        raise ValueError(
            f"Perturbation {variant_id!r} is for {variant.domain}/{variant.task_id}, "
            f"but loaded task is {domain}/{task_id}"
        )
    current_hash = source_task_sha256(task_path)
    if (
        variant.source_task_sha256
        and variant.source_task_sha256 != current_hash
        and not args.allow_stale_perturbation
    ):
        raise ValueError(
            f"Perturbation {variant_id!r} was generated for a different task file hash. "
            "Pass --allow-stale-perturbation to override."
        )
    return PerturbationConfig(
        variant=variant,
        spec_path=str(row.get("variant_path") or row.get("spec_path") or ""),
        index_path=str(index_path),
    )


def _final_output_from_trace_actions(actions: list) -> str:
    for action in reversed(actions):
        if (
            str(getattr(action, "surface", "")) == "communication"
            and getattr(action, "target_role", "") == "user"
        ):
            return str(getattr(action, "content", "") or "")
    return ""


def _is_degenerate_empty_trace(trace: object) -> bool:
    """Detect harness runs that produced no usable native agent transcript."""

    if getattr(trace, "error", None):
        return False
    if int(getattr(trace, "n_actions", 0) or 0) > 1:
        return False
    if int(getattr(trace, "n_tool_calls", 0) or 0) != 0:
        return False
    final_output = _final_output_from_trace_actions(getattr(trace, "actions", []) or [])
    return not final_output.strip()


@dataclass
class RunContextBundle:
    args: argparse.Namespace
    task_path: Path
    task: Any
    catalog: Any
    perturbation: PerturbationConfig | None
    harness: str
    trace_dir: Path
    output_dir: Path | None
    run_id: str
    artifact_prefix: str
    trace_path: Path
    writer: TraceWriter
    runner: BenchmarkRunner


@dataclass
class RunResultBundle:
    trace: Any | None = None
    metrics: Any | None = None
    violations: list[Any] = field(default_factory=list)
    perturbation_result: PerturbationEvaluation | None = None
    trace_path: Path | None = None


def build_run_context(
    args: argparse.Namespace,
    *,
    task_path: Path,
    pkg_root: Path,
    repo_root: Path,
    harness: str,
) -> RunContextBundle:
    """Load task state and construct the runner for one concrete run."""

    task, catalog = load_task_with_tools(task_path)
    perturbation = _load_perturbation_config(
        args,
        task_id=task.task_id,
        domain=task.domain,
        task_path=task_path,
    )
    trace_dir = _artifact_scoped_dir(Path(args.trace_dir), harness, args.model)
    output_dir = (
        _artifact_scoped_dir(Path(args.output), harness, args.model)
        if args.output else None
    )
    run_id = next_run_id(
        model=args.model,
        framework_slug=framework_run_id_slug(args.framework),
        harness_slug=harness,
    )
    artifact_prefix = _artifact_prefix(task.task_id, perturbation)
    trace_path = trace_dir / f"{artifact_prefix}_{run_id}.jsonl"
    writer = TraceWriter(trace_path)

    fixtures_root = repo_root / "fixtures"
    workspaces_root = Path(os.environ.get(
        "MASP_WORKSPACES_ROOT", str(pkg_root / "workspaces"),
    ))
    workspace_manager = WorkspaceManager(
        fixtures_root=fixtures_root,
        workspaces_root=workspaces_root,
    )
    runner = BenchmarkRunner(
        task,
        catalog,
        model=args.model,
        framework=args.framework,
        harness=harness,
        trace_writer=writer,
        max_turns=args.max_turns,
        workspace_manager=workspace_manager,
        agent_timeout=getattr(args, "agent_timeout", 300),
        judge_model=args.judge_model,
        skip_judge=args.skip_judge,
        judge_workers=args.judge_workers,
        perturbation=perturbation,
    )
    return RunContextBundle(
        args=args,
        task_path=task_path,
        task=task,
        catalog=catalog,
        perturbation=perturbation,
        harness=harness,
        trace_dir=trace_dir,
        output_dir=output_dir,
        run_id=run_id,
        artifact_prefix=artifact_prefix,
        trace_path=trace_path,
        writer=writer,
        runner=runner,
    )


def _print_run_header(ctx: RunContextBundle) -> None:
    print(f"Loading task: {ctx.task_path}")
    print(f"  task_id:    {ctx.task.task_id}")
    print(f"  domain:     {ctx.task.domain}")
    print(f"  agents:     {[a.role for a in ctx.task.agents]}")
    print(f"  rules:      {len(ctx.task.access_rules)}")
    print(f"  tools:      {len(ctx.catalog.tools)} in catalog")
    if ctx.perturbation is not None:
        print(f"  perturbation: {ctx.perturbation.variant_id} ({ctx.perturbation.attack_type})")
    print()
    print(
        f"Running (framework={ctx.args.framework}, "
        f"harness={ctx.harness}, model={ctx.args.model})..."
    )


def _print_trace_summary(trace: Any) -> None:
    print(f"  actions:    {trace.n_actions}")
    print(f"  tool_calls: {trace.n_tool_calls}")
    print(f"  comms:      {trace.n_communications}")
    if trace.error:
        print(f"  runner error: {trace.error}")
    if trace.warnings:
        for warning in trace.warnings:
            print(f"  warning: {warning}")
    print()


def _write_access_decisions(ctx: RunContextBundle, decisions: list[Any]) -> list[Any]:
    for d in decisions:
        ctx.writer.write_event(AccessDecisionEvent(
            trace_id=ctx.run_id,
            action_sequence_num=d.action_sequence_num or 0,
            violated=d.violated,
            violation_code=d.violation_code.value if d.violation_code else None,
            severity=d.severity,
            rule_description=d.rule.description if d.rule else None,
        ))

    violations = [d for d in decisions if d.violated]
    for v in violations:
        rule_desc = v.rule.description if v.rule else ""
        severity = f" ({v.severity})" if v.severity else ""
        print(
            f"  VIOLATION #{v.action_sequence_num}: {v.violation_code.value}"
            f"{severity} — {rule_desc}"
        )
    if not violations:
        print("  No violations detected.")
    print()
    return violations


def _evaluate_operational_safety(ctx: RunContextBundle, trace: Any) -> float | None:
    hub_role = ctx.task.metadata.get("hub_role")
    if not isinstance(hub_role, str):
        hub_role = ctx.task.agents[0].role
    return asyncio.run(
        evaluate_operational_governance(
            trace.actions,
            ctx.task.ground_truth_tool_paths or {},
            hub_role,
            access_rules=ctx.task.access_rules,
            model=ctx.args.judge_model,
            skip=ctx.args.skip_judge,
            judge_workers=ctx.args.judge_workers,
        )
    )


def _evaluate_perturbation_if_needed(
    ctx: RunContextBundle,
    trace: Any,
    decisions: list[Any],
) -> PerturbationEvaluation | None:
    if ctx.perturbation is None:
        return None
    print("Evaluating perturbation stability...")
    result = asyncio.run(
        evaluate_perturbation(
            variant=ctx.perturbation.variant,
            actions=trace.actions,
            decisions=decisions,
            final_output=_final_output_from_trace_actions(trace.actions),
            task_completion_score=trace.task_completion_score,
            clean_task_completion_score=None,
            model=ctx.args.judge_model,
            skip_judge=ctx.args.skip_judge,
        )
    )
    trace.perturbation_delivered = result.delivered
    trace.stability_score = result.stability_score
    trace.stable = result.stable
    stability_display = (
        f"{result.stability_score:.2f}"
        if result.stability_score is not None else "n/a"
    )
    print(
        "  perturbation: "
        f"delivered={result.delivered}, "
        f"stable={result.stable}, "
        f"stability_score={stability_display}"
    )
    print()
    return result


def _print_metrics(metrics: Any) -> None:
    print("Metrics:")
    print("  L1 Boundary Compliance — Safety Adherence Rate:")
    print(f"    sar_tool:             {metrics.sar_tool:.2f}")
    print(f"    sar_resource:         {metrics.sar_resource:.2f}")
    print(f"    sar_flow:             {metrics.sar_flow:.2f}")
    print(f"    sar_avg:              {metrics.sar_avg:.2f}")
    avs = f"{metrics.avs:.2f}" if metrics.avs is not None else "n/a"
    print("  L2 Execution Fidelity:")
    print(f"    avs:                  {avs}")
    print(f"    tcr:                  {metrics.tcr:.2f}")
    if metrics.violations_by_code:
        print(f"  violations:        {metrics.violations_by_code}")
    print()


def _metric_event_fields(metrics: Any | None) -> dict[str, Any]:
    if metrics is None:
        return {}
    return {
        **metrics.paper_fields(),
        **metrics.diagnostic_fields(),
    }


def _write_trace_end(
    ctx: RunContextBundle,
    result: RunResultBundle,
    *,
    error: str | None = None,
) -> None:
    trace = result.trace
    metrics = result.metrics
    ctx.writer.write_event(TraceEnd(
        trace_id=ctx.run_id,
        n_actions=trace.n_actions if trace else 0,
        n_tool_calls=trace.n_tool_calls if trace else 0,
        n_communications=trace.n_communications if trace else 0,
        n_violations=len(result.violations),
        **_metric_event_fields(metrics),
        perturbation=_perturbation_trace_payload(
            ctx.perturbation,
            result.perturbation_result,
        ),
        violations_by_code=(metrics.violations_by_code if metrics else {}),
        wall_time_s=(trace.duration_seconds or 0.0) if trace else 0.0,
        error=(error or (trace.error if trace and trace.error else None)),
    ))


def run_once(
    ctx: RunContextBundle,
    result: RunResultBundle | None = None,
) -> RunResultBundle:
    """Execute one run, score it, and write trace-side events."""

    result = result or RunResultBundle(trace_path=ctx.trace_path)
    try:
        trace = ctx.runner.run(run_id=ctx.run_id)
        result.trace = trace
        _print_trace_summary(trace)

        if _is_degenerate_empty_trace(trace):
            raise RuntimeError(
                "Degenerate empty harness run: no tool calls and empty final output. "
                "Native agent session files may be missing or the harness failed silently."
            )

        print("Checking access rules...")
        decisions = check_trace(
            trace.actions,
            ctx.task.access_rules,
            domain=ctx.task.domain,
        )
        trace.decisions = decisions
        result.violations = _write_access_decisions(ctx, decisions)

        operational_safety_score = _evaluate_operational_safety(ctx, trace)
        metrics = compute_metrics(
            trace.run_id,
            trace.actions,
            decisions,
            resource_tools={t.name for t in ctx.catalog.tools if t.is_resource},
            task_completion_score=trace.task_completion_score,
            operational_safety_score=operational_safety_score,
        )
        result.metrics = metrics
        result.perturbation_result = _evaluate_perturbation_if_needed(
            ctx,
            trace,
            decisions,
        )
        _print_metrics(metrics)
        _write_trace_end(ctx, result)
        return result
    except Exception as exc:
        error = result.trace.error if result.trace and result.trace.error else str(exc)
        _write_trace_end(ctx, result, error=error)
        raise


def build_report_payload(ctx: RunContextBundle, result: RunResultBundle) -> dict[str, Any]:
    """Build the JSON report payload for a completed run."""

    trace = result.trace
    metrics = result.metrics
    if trace is None or metrics is None:
        raise RuntimeError("Cannot build report before trace and metrics are available")
    report = {
        "task_id": ctx.task.task_id,
        "run_id": trace.run_id,
        "framework": trace.framework,
        "llm": trace.llm,
        "n_actions": trace.n_actions,
        "n_tool_calls": trace.n_tool_calls,
        "n_communications": trace.n_communications,
        "n_violations": len(result.violations),
        **metrics.paper_fields(),
        **metrics.diagnostic_fields(),
        "deterministic_completion_score": trace.deterministic_completion_score,
        "deterministic_completion_weight": trace.deterministic_completion_weight,
        "deterministic_completion_contribution": (
            trace.deterministic_completion_contribution
        ),
        "llm_completion_score": trace.llm_completion_score,
        "llm_completion_weight": trace.llm_completion_weight,
        "llm_completion_contribution": trace.llm_completion_contribution,
        "trajectory_tcr": metrics.tcr,
        "completion_score_mode": COMPLETION_SCORE_MODE,
        "workspace_path": trace.workspace_path,
        "violations_by_code": metrics.violations_by_code,
        "violations_by_severity": metrics.violations_by_severity,
        "trace_file": str(result.trace_path or ctx.trace_path),
        "error": trace.error,
        "warnings": trace.warnings,
        "sde_native_bypass_evidence": [
            action.raw_event
            for action in trace.actions
            if isinstance(action.raw_event, dict)
            and action.raw_event.get("sde_native_bypass_detected") is True
        ],
    }
    perturbation_payload = _perturbation_trace_payload(
        ctx.perturbation,
        result.perturbation_result,
    )
    if perturbation_payload is not None:
        report["perturbation"] = perturbation_payload
    return report


def write_artifacts(ctx: RunContextBundle, result: RunResultBundle | None) -> None:
    """Close trace writer and write canonical trace/db/report artifacts."""

    ctx.writer.close()
    trace = result.trace if result else None
    trace_path = ctx.trace_path

    if trace is not None and ctx.args.framework == "clawteam":
        trace_path, canonical_run_id, canonical_llm = canonicalize_clawteam_artifacts(
            trace_path=trace_path,
            run_id=trace.run_id,
            harness=ctx.harness,
            agent_roles=[a.role for a in ctx.task.agents],
            results_dir=ctx.output_dir,
            task_id=ctx.task.task_id,
            current_llm=trace.llm,
            artifact_prefix=ctx.artifact_prefix,
        )
        trace.run_id = canonical_run_id
        trace.llm = canonical_llm
    if result is not None:
        result.trace_path = trace_path
    print(f"Trace saved to {trace_path}")

    if ctx.output_dir is not None and trace is not None:
        out_dir = ctx.output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        db_path = out_dir / f"{ctx.artifact_prefix}_{ctx.run_id}.sqlite"
        if ctx.runner.dump_bank_to_file(db_path):
            if ctx.args.framework == "clawteam" and trace.run_id != ctx.run_id:
                canonical_db_path = out_dir / f"{ctx.artifact_prefix}_{trace.run_id}.sqlite"
                db_path.replace(canonical_db_path)
                db_path = canonical_db_path
            print(f"Bank DB saved to {db_path}")

    ctx.runner.close_bank()

    if ctx.output_dir is None or result is None or result.trace is None or result.metrics is None:
        return
    out_dir = ctx.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    report = build_report_payload(ctx, result)
    out_path = out_dir / f"{ctx.artifact_prefix}_{result.trace.run_id}.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Report saved to {out_path}")


def cmd_run(args: argparse.Namespace) -> None:
    """Run a single task and evaluate."""

    pkg_root = Path(__file__).resolve().parent
    repo_root = pkg_root.parent
    task_path = resolve_task_path(args.task, project_root=pkg_root, cwd=Path.cwd())
    harness = _effective_harness(args)
    for repeat_index in range(args.repeat):
        if args.repeat > 1:
            print(f"=== Repeat {repeat_index + 1}/{args.repeat} ===")
        ctx = build_run_context(
            args,
            task_path=task_path,
            pkg_root=pkg_root,
            repo_root=repo_root,
            harness=harness,
        )
        result = RunResultBundle(trace_path=ctx.trace_path)
        _print_run_header(ctx)
        try:
            run_once(ctx, result)
        finally:
            write_artifacts(ctx, result)


def main() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(env_path)

    parser = argparse.ArgumentParser(
        prog="multi_agent",
        description="Multi-agent HarnessAudit runner",
    )
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run a single task")
    run_p.add_argument(
        "task",
        help=(
            "Task YAML path (e.g. tasks/finance/fin-t1.yaml, tasks/sde/sde1-t1.yaml, "
            "or shorthand fin-t1.yaml / tasks/fin-t1.yaml)"
        ),
    )
    run_p.add_argument(
        "--model",
        default="sonnet",
        help="LLM model label for trace metadata (default: sonnet)",
    )
    run_p.add_argument(
        "--framework",
        default="clawteam",
        choices=["clawteam", "oai", "adk"],
        help="MAS framework adapter (default: clawteam)",
    )
    run_p.add_argument(
        "--harness",
        default=None,
        choices=["claude", "codex", "openclaw", "oai", "adk"],
        help=(
            "Agent harness/artifact label. Defaults to claude for clawteam "
            "and to the framework name for --framework oai/adk."
        ),
    )
    run_p.add_argument("--agent-timeout", type=int, default=300,
                       help="Max seconds to wait for all agents to exit (default: 300)")
    run_p.add_argument(
        "--skip-judge",
        action="store_true",
        help="Skip all LLM-as-judge scoring; deterministic checkpoints still run",
    )
    run_p.add_argument(
        "--judge-model",
        default="gpt-5.4",
        help="GPT model for completion and operational judges (default: gpt-5.4)",
    )
    run_p.add_argument(
        "--judge-workers",
        type=int,
        default=4,
        help="Max concurrent LLM judge calls per task (default: 4; use 1 for serial)",
    )
    run_p.add_argument("--output", "-o", help="Output directory for results JSON")
    _default_trace = str(Path(__file__).resolve().parent / "traces")
    run_p.add_argument("--trace-dir", default=_default_trace,
                       help="Directory for JSONL trace files")
    run_p.add_argument("--max-turns", type=int, default=30,
                       help="Maximum framework turns per run (default: 30)")
    run_p.add_argument("--repeat", type=int, default=1,
                       help="Repeat the same task sequentially (default: 1)")
    run_p.add_argument(
        "--perturbation-id",
        default=None,
        help="Run a specific safety perturbation variant ID",
    )
    run_p.add_argument(
        "--perturbation-index",
        default=str(default_index_path()),
        help="Path to perturbation index.jsonl",
    )
    run_p.add_argument(
        "--perturbation-specs-dir",
        default=None,
        help="Reserved for compatibility; variants are resolved through --perturbation-index",
    )
    run_p.add_argument(
        "--allow-stale-perturbation",
        action="store_true",
        help="Allow running a perturbation whose source_task_sha256 differs from the task file",
    )

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
