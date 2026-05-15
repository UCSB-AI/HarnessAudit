"""single_agent CLI - run single-agent tasks and evaluate access control violations.

Usage:
    python -m single_agent run sa_tasks/finance/sa-fin-t1.yaml
    python -m single_agent run sa-fin-t1.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from multi_agent.checker import check_trace, compute_metrics
from multi_agent.operational_judge import evaluate_operational_governance
from multi_agent.runner import (
    BenchmarkRunner,
    framework_run_id_slug,
    next_run_id,
)
from multi_agent.trace import AccessDecisionEvent, TraceEnd, TraceWriter
from multi_agent.workspace import WorkspaceManager
from single_agent.loader import load_sa_task_with_tools, resolve_sa_task_path


def _scoped_dir(base_dir: Path, scope: str) -> Path:
    """Store artifacts under a framework-specific child directory."""
    return base_dir if base_dir.name == scope else base_dir / scope


def _normalize_model(name: str) -> str:
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", name)


def _metric_event_fields(metrics: object | None) -> dict[str, object]:
    if metrics is None:
        return {}
    return {
        **metrics.paper_fields(),
        **metrics.diagnostic_fields(),
    }


def cmd_run(args: argparse.Namespace) -> None:
    """Run a single SA task and evaluate."""
    args.model = _normalize_model(args.model)
    pkg_root = Path(__file__).resolve().parent        # single_agent/
    repo_root = pkg_root.parent                       # HarnessAudit/
    task_path = resolve_sa_task_path(args.task, project_root=pkg_root, cwd=Path.cwd())

    for repeat_index in range(args.repeat):
        if args.repeat > 1:
            print(f"=== Repeat {repeat_index + 1}/{args.repeat} ===")

        print(f"Loading SA task: {task_path}")
        task, catalog = load_sa_task_with_tools(task_path)

        print(f"  task_id:    {task.task_id}")
        print(f"  domain:     {task.domain}")
        print(f"  agent:      {task.agents[0].role}")
        print(f"  rules:      {len(task.access_rules)}")
        print(f"  tools:      {len(catalog.tools)} in catalog")
        print()

        trace_dir = _scoped_dir(Path(args.trace_dir), args.framework)
        output_dir = _scoped_dir(Path(args.output), args.framework) if args.output else None
        run_id = next_run_id(
            args.model,
            framework_slug=framework_run_id_slug(args.framework),
            harness_slug=args.framework,
        )
        trace_path = trace_dir / f"{task.task_id}_{run_id}.jsonl"
        writer = TraceWriter(trace_path)

        workspace_manager = WorkspaceManager(
            fixtures_root=repo_root / "fixtures",
            workspaces_root=Path(os.environ.get(
                "MASP_WORKSPACES_ROOT", str(repo_root / "workspaces"),
            )),
        )

        # Use SA bank factory instead of MAS bank factory
        from single_agent import banks as sa_banks

        print(f"Running (framework={args.framework}, model={args.model})...")
        runner = BenchmarkRunner(
            task, catalog, model=args.model, framework=args.framework,
            trace_writer=writer, max_turns=args.max_turns,
            workspace_manager=workspace_manager,
            bank_factory=sa_banks.get_bank,
            harness="openclaw",
            agent_timeout=args.agent_timeout,
            judge_model=args.judge_model,
            skip_judge=args.skip_judge,
            judge_workers=args.judge_workers,
        )

        metrics = None
        violations: list = []
        trace = None

        try:
            trace = runner.run(run_id=run_id)

            print(f"  actions:    {trace.n_actions}")
            print(f"  tool_calls: {trace.n_tool_calls}")
            if trace.error:
                print(f"  runner error: {trace.error}")
                raise RuntimeError(trace.error)
            print()

            print("Checking access rules...")
            decisions = check_trace(
                trace.actions, task.access_rules,
                domain=task.domain,
            )
            trace.decisions = decisions

            for d in decisions:
                writer.write_event(AccessDecisionEvent(
                    trace_id=run_id,
                    action_sequence_num=d.action_sequence_num or 0,
                    violated=d.violated,
                    violation_code=d.violation_code.value if d.violation_code else None,
                    severity=d.severity,
                    rule_description=d.rule.description if d.rule else None,
                ))

            violations = [d for d in decisions if d.violated]
            for v in violations:
                rule_desc = v.rule.description if v.rule else ""
                print(f"  VIOLATION #{v.action_sequence_num}: "
                      f"{v.violation_code.value} ({v.severity}) — {rule_desc}")

            if not violations:
                print("  No violations detected.")
            print()

            hub_role = task.metadata.get("hub_role")
            if not isinstance(hub_role, str):
                hub_role = task.agents[0].role
            operational_safety_score = asyncio.run(
                evaluate_operational_governance(
                    trace.actions,
                    task.ground_truth_tool_paths or {},
                    hub_role,
                    access_rules=task.access_rules,
                    model=args.judge_model,
                    skip=args.skip_judge,
                    judge_workers=args.judge_workers,
                )
            )

            metrics = compute_metrics(
                trace.run_id, trace.actions, decisions,
                resource_tools={t.name for t in catalog.tools if t.is_resource},
                task_completion_score=trace.task_completion_score,
                operational_safety_score=operational_safety_score,
            )
            avs = f"{metrics.avs:.2f}" if metrics.avs is not None else "n/a"
            print("Metrics:")
            print("  L1 Boundary Compliance — Safety Adherence Rate:")
            print(f"    sar_tool:              {metrics.sar_tool:.2f}")
            print(f"    sar_resource:          {metrics.sar_resource:.2f}")
            print(f"    sar_flow:              {metrics.sar_flow:.2f}")
            print(f"    sar_avg:               {metrics.sar_avg:.2f}")
            print("  L2 Execution Fidelity:")
            print(f"    avs:                   {avs}")
            print(f"    tcr:                   {metrics.tcr:.2f}")
            if metrics.violations_by_code:
                print(f"  violations:             {metrics.violations_by_code}")
            print()

            writer.write_event(TraceEnd(
                trace_id=run_id,
                n_actions=trace.n_actions,
                n_tool_calls=trace.n_tool_calls,
                n_communications=trace.n_communications,
                n_violations=len(violations),
                **_metric_event_fields(metrics),
                violations_by_code=metrics.violations_by_code,
                wall_time_s=trace.duration_seconds or 0.0,
                error=trace.error,
            ))

        except Exception as e:
            writer.write_event(TraceEnd(
                trace_id=run_id,
                n_actions=trace.n_actions if trace else 0,
                n_tool_calls=trace.n_tool_calls if trace else 0,
                n_communications=trace.n_communications if trace else 0,
                n_violations=len(violations),
                wall_time_s=(trace.duration_seconds or 0.0) if trace else 0.0,
                error=(trace.error if trace and trace.error else str(e)),
            ))
            raise

        finally:
            writer.close()
            print(f"Trace saved to {trace_path}")
            if output_dir is not None and trace is not None:
                output_dir.mkdir(parents=True, exist_ok=True)
                db_path = output_dir / f"{task.task_id}_{run_id}.sqlite"
                if runner.dump_bank_to_file(db_path):
                    print(f"Bank DB saved to {db_path}")
            runner.close_bank()

        if output_dir is not None:
            assert trace is not None
            assert metrics is not None
            output_dir.mkdir(parents=True, exist_ok=True)
            report = {
                "task_id": task.task_id,
                "run_id": trace.run_id,
                "framework": trace.framework,
                "llm": trace.llm,
                "n_actions": trace.n_actions,
                "n_tool_calls": trace.n_tool_calls,
                "n_communications": trace.n_communications,
                "n_violations": len(violations),
                **metrics.paper_fields(),
                **metrics.diagnostic_fields(),
                "violations_by_code": metrics.violations_by_code,
                "violations_by_severity": metrics.violations_by_severity,
                "trace_file": str(trace_path),
                "error": trace.error,
                "warnings": trace.warnings,
            }
            out_path = output_dir / f"{task.task_id}_{trace.run_id}.json"
            with open(out_path, "w") as f:
                json.dump(report, f, indent=2)
            print(f"Report saved to {out_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="single_agent",
        description="Single-agent HarnessAudit runner",
    )
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run a single SA task")
    run_p.add_argument("task", help="SA task YAML path")
    run_p.add_argument("--model", default="anthropic/claude-sonnet-4-6")
    run_p.add_argument(
        "--framework",
        default="openclaw_local",
        choices=["openclaw_local"],
    )
    _sa_pkg = Path(__file__).resolve().parent
    run_p.add_argument(
        "--output",
        "-o",
        help="Output directory (default: single_agent/results/)",
    )
    run_p.add_argument("--trace-dir", default=str(_sa_pkg / "traces"))
    run_p.add_argument("--max-turns", type=int, default=15)
    run_p.add_argument("--repeat", type=int, default=1)
    run_p.add_argument("--agent-timeout", type=int, default=300)
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
    return parser


def main() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(env_path)

    parser = build_parser()
    args = parser.parse_args()
    if args.command == "run":
        cmd_run(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
