"""MASP-Bench CLI — run tasks and evaluate access control violations.

Usage:
    python -m masp_bench run tasks/fin-t1.yaml
    python -m masp_bench run tasks/fin-t1.yaml --model gpt-4.1 --output results/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

from masp_bench.loader import load_task_with_tools
from masp_bench.runner import OpenAIAgentsRunner
from masp_bench.checker import check_trace, compute_metrics
from masp_bench.trace import TraceWriter, AccessDecisionEvent, TraceEnd


def cmd_run(args: argparse.Namespace) -> None:
    """Run a single task and evaluate."""
    task_path = Path(args.task)
    print(f"Loading task: {task_path}")
    task, catalog = load_task_with_tools(task_path)

    print(f"  task_id:    {task.task_id}")
    print(f"  domain:     {task.domain}")
    print(f"  agents:     {[a.role for a in task.agents]}")
    print(f"  rules:      {len(task.access_rules)}")
    print(f"  tools:      {len(catalog.tools)} in catalog")
    print()

    # --- Trace writer setup ---
    run_id = f"oai-{uuid.uuid4().hex[:8]}"
    trace_dir = Path(args.trace_dir)
    trace_path = trace_dir / f"{task.task_id}_{run_id}.jsonl"
    writer = TraceWriter(trace_path)

    # --- Run ---
    print(f"Running (OpenAI Agents SDK, model={args.model})...")
    runner = OpenAIAgentsRunner(
        task, catalog, model=args.model, trace_writer=writer,
    )

    metrics = None
    violations: list = []
    trace = None

    try:
        trace = runner.run(defense_mode=args.defense, run_id=run_id)

        print(f"  actions:    {trace.n_actions}")
        print(f"  tool_calls: {trace.n_tool_calls}")
        print(f"  comms:      {trace.n_communications}")
        print()

        # --- Check ---
        print("Checking access rules...")
        decisions = check_trace(trace.actions, task.access_rules)
        trace.decisions = decisions

        # Emit access decisions to trace
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

        # --- Metrics ---
        metrics = compute_metrics(trace.run_id, decisions, trace.task_completed or False)
        print("Metrics:")
        print(f"  access_score: {metrics.access_score:.2f}")
        print(f"  tsr:          {metrics.tsr:.0f}")
        print(f"  qsr:          {metrics.qsr:.0f}")
        if metrics.violations_by_code:
            print(f"  violations:   {metrics.violations_by_code}")
        print()

        # Emit TraceEnd (success)
        writer.write_event(TraceEnd(
            trace_id=run_id,
            n_actions=trace.n_actions,
            n_tool_calls=trace.n_tool_calls,
            n_communications=trace.n_communications,
            n_violations=len(violations),
            access_score=metrics.access_score,
            tsr=metrics.tsr,
            qsr=metrics.qsr,
            violations_by_code=metrics.violations_by_code,
            wall_time_s=trace.duration_seconds or 0.0,
            error=None,
        ))

    except Exception as e:
        # Emit TraceEnd with error
        writer.write_event(TraceEnd(
            trace_id=run_id,
            n_actions=trace.n_actions if trace else 0,
            n_violations=len(violations),
            access_score=metrics.access_score if metrics else 0.0,
            wall_time_s=trace.duration_seconds if trace else 0.0,
            error=str(e),
        ))
        raise

    finally:
        writer.close()
        print(f"Trace saved to {trace_path}")

    # --- Save report ---
    if args.output:
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "task_id": task.task_id,
            "run_id": trace.run_id,
            "framework": trace.framework,
            "llm": trace.llm,
            "defense_mode": trace.defense_mode,
            "n_actions": trace.n_actions,
            "n_violations": len(violations),
            "access_score": metrics.access_score,
            "tsr": metrics.tsr,
            "qsr": metrics.qsr,
            "violations_by_code": metrics.violations_by_code,
            "violations_by_severity": metrics.violations_by_severity,
            "trace_file": str(trace_path),
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
    run_p.add_argument("task", help="Path to task YAML file")
    run_p.add_argument("--model", default="gpt-4.1-mini",
                       help="LLM model (default: gpt-4.1-mini)")
    run_p.add_argument("--defense", default="monitor",
                       choices=["monitor", "declared", "enforced"])
    run_p.add_argument("--output", "-o", help="Output directory for results JSON")
    run_p.add_argument("--trace-dir", default="traces",
                       help="Directory for JSONL trace files (default: traces/)")

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
