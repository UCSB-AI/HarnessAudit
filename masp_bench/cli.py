"""MASP-Bench CLI — run tasks and evaluate access control violations.

Usage:
    python -m masp_bench run tasks/finance/fin-t1.yaml
    python -m masp_bench run fin-t1.yaml
    python -m masp_bench run tasks/finance/fin-t1.yaml --model gpt-4.1 --output results/
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
from masp_bench.loader import load_task_with_tools, resolve_task_path
from masp_bench.runner import OpenAIAgentsRunner, next_run_id
from masp_bench.trace import AccessDecisionEvent, TraceEnd, TraceWriter
from masp_bench.workspace import WorkspaceManager


def _normalize_model(name: str) -> str:
    """Strip date suffixes (e.g. 'gpt-5.4-2026-03-05' → 'gpt-5.4')."""
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", name)


def cmd_run(args: argparse.Namespace) -> None:
    """Run a single task and evaluate."""
    args.model = _normalize_model(args.model)
    project_root = Path(__file__).resolve().parent.parent
    task_path = resolve_task_path(args.task, project_root=project_root, cwd=Path.cwd())
    for repeat_index in range(args.repeat):
        if args.repeat > 1:
            print(f"=== Repeat {repeat_index + 1}/{args.repeat} ===")

        print(f"Loading task: {task_path}")
        task, catalog = load_task_with_tools(task_path)

        print(f"  task_id:    {task.task_id}")
        print(f"  domain:     {task.domain}")
        print(f"  agents:     {[a.role for a in task.agents]}")
        print(f"  rules:      {len(task.access_rules)}")
        print(f"  tools:      {len(catalog.tools)} in catalog")
        print()

        trace_dir = Path(args.trace_dir)
        output_dir = (
            Path(args.output)
            if args.output
            else Path(__file__).resolve().parent.parent / "results"
        )
        run_id = next_run_id([trace_dir, output_dir], args.model)
        trace_path = trace_dir / f"{task.task_id}_{run_id}.jsonl"
        writer = TraceWriter(trace_path)

        # --- Workspace manager for SDE real fixtures ---
        fixtures_root = project_root / "fixtures"
        workspaces_root = Path(os.environ.get(
            "MASP_WORKSPACES_ROOT", str(project_root / "workspaces"),
        ))
        workspace_manager = WorkspaceManager(
            fixtures_root=fixtures_root,
            workspaces_root=workspaces_root,
        )

        print(f"Running (OpenAI Agents SDK, model={args.model})...")
        runner = OpenAIAgentsRunner(
            task, catalog, model=args.model, trace_writer=writer,
            max_turns=args.max_turns,
            workspace_manager=workspace_manager,
        )

        metrics = None
        violations: list = []
        trace = None

        try:
            trace = runner.run(defense_mode=args.defense, run_id=run_id)

            print(f"  actions:    {trace.n_actions}")
            print(f"  tool_calls: {trace.n_tool_calls}")
            print(f"  comms:      {trace.n_communications}")
            if trace.error:
                print(f"  runner error: {trace.error}")
            print()

            print("Checking access rules...")
            decisions = check_trace(trace.actions, task.access_rules, domain=task.domain)
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
                print(f"  VIOLATION #{v.action_sequence_num}: {v.violation_code.value} "
                      f"({v.severity}) — {rule_desc}")

            if not violations:
                print("  No violations detected.")
            print()

            metrics = compute_metrics(
                trace.run_id, trace.actions, decisions, trace.task_completed or False,
            )
            op = (
                f"{metrics.operation_score:.2f}"
                if metrics.operation_score is not None else "n/a"
            )
            info = (
                f"{metrics.information_score:.2f}"
                if metrics.information_score is not None else "n/a"
            )
            print("Metrics:")
            print("  Layer 1 — severity-summed (deduplicated):")
            print(f"    access_score:      {metrics.access_score:.2f}")
            print("  Layer 2 — per-branch:")
            n_tc = trace.n_tool_calls
            n_co = trace.n_communications
            print(f"    operation_score:   {op}    (V-OT/V-OR over {n_tc} tool calls)")
            print(f"    information_score: {info}    (V-ID/V-IC over {n_co} communications)")
            print(f"    privacy_score:     {metrics.privacy_score:.2f}    (mean of available)")
            print("  Layer 3 — joint utility + safety:")
            print(f"    tsr:               {metrics.tsr:.0f}")
            print(f"    pqsr@0.7:          {metrics.pqsr:.0f}")
            if metrics.violations_by_code:
                print(f"  violations:        {metrics.violations_by_code}")
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

            # Explicitly release per-run SQLite resources once optional dump is done.
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
                "defense_mode": trace.defense_mode,
                "n_actions": trace.n_actions,
                "n_tool_calls": trace.n_tool_calls,
                "n_communications": trace.n_communications,
                "n_violations": len(violations),
                # Layer 1
                "access_score": metrics.access_score,
                # Layer 2
                "operation_score": metrics.operation_score,
                "information_score": metrics.information_score,
                "privacy_score": metrics.privacy_score,
                # Layer 3
                "tsr": metrics.tsr,
                "pqsr": metrics.pqsr,
                "qsr": metrics.qsr,  # alias for pqsr (backward compat)
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
    # Load .env from project root
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(env_path)

    if not os.environ.get("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY not set. Add it to .env or export it.", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(
        prog="masp-bench",
        description="MASP-Bench: Benchmarking Access Control Safety in Multi-Agent LLM Systems",
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
    run_p.add_argument("--model", default="gpt-4.1-mini",
                       help="LLM model (default: gpt-4.1-mini)")
    run_p.add_argument("--defense", default="monitor",
                       choices=["monitor", "declared", "enforced"])
    run_p.add_argument("--output", "-o", help="Output directory for results JSON")
    run_p.add_argument("--trace-dir", default="traces",
                       help="Directory for JSONL trace files (default: traces/)")
    run_p.add_argument("--max-turns", type=int, default=30,
                       help="Maximum OpenAI Agents SDK turns per run (default: 30)")
    run_p.add_argument("--repeat", type=int, default=1,
                       help="Repeat the same task sequentially (default: 1)")

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
