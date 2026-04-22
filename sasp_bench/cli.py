"""SASP-Bench CLI — run single-agent tasks and evaluate access control violations.

Usage:
    python -m sasp_bench run sa_tasks/finance/sa-fin-t1.yaml
    python -m sasp_bench run sa-fin-t1.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from masp_bench.checker import check_trace, compute_metrics
from masp_bench.runner import BenchmarkRunner, framework_run_id_slug, next_run_id
from masp_bench.trace import AccessDecisionEvent, TraceEnd, TraceWriter
from masp_bench.workspace import WorkspaceManager

from sasp_bench.loader import load_sa_task_with_tools, resolve_sa_task_path


def _normalize_model(name: str) -> str:
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", name)


def cmd_run(args: argparse.Namespace) -> None:
    """Run a single SA task and evaluate."""
    args.model = _normalize_model(args.model)
    pkg_root = Path(__file__).resolve().parent        # sasp_bench/
    repo_root = pkg_root.parent                       # MASP_Bench/
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

        trace_dir = Path(args.trace_dir)
        output_dir = (
            Path(args.output) if args.output
            else pkg_root / "results"
        )
        run_id = next_run_id(
            args.model,
            framework_slug=framework_run_id_slug(args.framework),
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
        from sasp_bench import banks as sa_banks

        print(f"Running (framework={args.framework}, model={args.model})...")
        runner = BenchmarkRunner(
            task, catalog, model=args.model, framework=args.framework,
            trace_writer=writer, max_turns=args.max_turns,
            workspace_manager=workspace_manager,
            bank_factory=sa_banks.get_bank,
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
            print()

            print("Checking access rules...")
            scope_keys = {
                t.name: t.dedup_scope_keys
                for t in catalog.tools if t.dedup_scope_keys
            }
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
                    effect=d.effect,
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

            metrics = compute_metrics(
                trace.run_id, trace.actions, decisions,
                trace.task_completed or False,
            )
            op = (f"{metrics.op_privacy_score:.2f}"
                  if metrics.op_privacy_score is not None else "n/a")
            info = (f"{metrics.info_privacy_score:.2f}"
                    if metrics.info_privacy_score is not None else "n/a")
            print("Metrics:")
            print(f"    access_score:        {metrics.access_score:.2f}")
            print(f"    op_privacy_score:    {op}")
            print(f"    info_privacy_score:  {info}")
            print(f"    tsr:                 {metrics.tsr:.0f}")
            print(f"    pqsr@0.7:            {metrics.pqsr:.0f}")
            if metrics.violations_by_code:
                print(f"    violations:          {metrics.violations_by_code}")
            print()

            writer.write_event(TraceEnd(
                trace_id=run_id,
                n_actions=trace.n_actions,
                n_tool_calls=trace.n_tool_calls,
                n_communications=trace.n_communications,
                n_violations=len(violations),
                access_score=metrics.access_score,
                tsr=metrics.tsr,
                qsr=metrics.pqsr,
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
                access_score=metrics.access_score if metrics else 0.0,
                wall_time_s=(trace.duration_seconds or 0.0) if trace else 0.0,
                error=(trace.error if trace and trace.error else str(e)),
            ))
            raise

        finally:
            writer.close()
            print(f"Trace saved to {trace_path}")
            if args.output and trace is not None:
                out_dir = Path(args.output)
                out_dir.mkdir(parents=True, exist_ok=True)
                db_path = out_dir / f"{task.task_id}_{run_id}.sqlite"
                if runner.dump_bank_to_file(db_path):
                    print(f"Bank DB saved to {db_path}")
            runner.close_bank()

        if args.output:
            assert trace is not None
            assert metrics is not None
            out_dir = Path(args.output)
            out_dir.mkdir(parents=True, exist_ok=True)
            report = {
                "task_id": task.task_id,
                "run_id": trace.run_id,
                "framework": trace.framework,
                "llm": trace.llm,
                "defense_mode": "monitor",
                "n_actions": trace.n_actions,
                "n_tool_calls": trace.n_tool_calls,
                "n_communications": trace.n_communications,
                "n_violations": len(violations),
                "access_score": metrics.access_score,
                "op_privacy_score": metrics.op_privacy_score,
                "info_privacy_score": metrics.info_privacy_score,
                "tsr": metrics.tsr,
                "pqsr": metrics.pqsr,
                "violations_by_code": metrics.violations_by_code,
                "violations_by_severity": metrics.violations_by_severity,
                "trace_file": str(trace_path),
                "error": trace.error,
            }
            out_path = out_dir / f"{task.task_id}_{trace.run_id}.json"
            with open(out_path, "w") as f:
                json.dump(report, f, indent=2)
            print(f"Report saved to {out_path}")


def main() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(env_path)

    if not os.environ.get("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(
        prog="sasp-bench",
        description="SASP-Bench: Single-Agent Safety & Privacy Benchmark",
    )
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run a single SA task")
    run_p.add_argument("task", help="SA task YAML path")
    run_p.add_argument("--model", default="claude-sonnet-4")
    run_p.add_argument("--framework", default="clawteam",
                       choices=["clawteam"])
    _sa_pkg = Path(__file__).resolve().parent
    run_p.add_argument("--output", "-o", help="Output directory (default: sasp_bench/results/)")
    run_p.add_argument("--trace-dir", default=str(_sa_pkg / "traces"))
    run_p.add_argument("--max-turns", type=int, default=15)
    run_p.add_argument("--repeat", type=int, default=1)

    args = parser.parse_args()
    if args.command == "run":
        cmd_run(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
