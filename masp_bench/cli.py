"""MASP-Bench CLI — run tasks and evaluate access control violations."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

import sqlite3

from masp_bench.checker import check_trace, compute_metrics
from masp_bench.completion_judge import evaluate_completion_checkpoints
from masp_bench.frameworks.harness_backbone import canonicalize_clawteam_artifacts
from masp_bench.loader import load_task_with_tools, resolve_task_path
from masp_bench.operational_judge import evaluate_operational_governance
from masp_bench.runner import (
    BenchmarkRunner,
    framework_run_id_slug,
    next_run_id,
)
from masp_bench.schemas.actions import ActionSurface, ObservableAction
from masp_bench.trace import AccessDecisionEvent, TraceEnd, TraceWriter
from masp_bench.workspace import WorkspaceManager


def cmd_run(args: argparse.Namespace) -> None:
    """Run a single task and evaluate."""
    pkg_root = Path(__file__).resolve().parent        # masp_bench/
    repo_root = pkg_root.parent                       # MASP_Bench/
    task_path = resolve_task_path(args.task, project_root=pkg_root, cwd=Path.cwd())
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
        run_id = next_run_id(
            model=args.model,
            framework_slug=framework_run_id_slug(args.framework),
            harness_slug=args.harness,
        )
        trace_path = trace_dir / f"{task.task_id}_{run_id}.jsonl"
        writer = TraceWriter(trace_path)

        # --- Workspace manager for SDE real fixtures ---
        fixtures_root = repo_root / "fixtures"
        workspaces_root = Path(os.environ.get(
            "MASP_WORKSPACES_ROOT", str(pkg_root / "workspaces"),
        ))
        workspace_manager = WorkspaceManager(
            fixtures_root=fixtures_root,
            workspaces_root=workspaces_root,
        )

        print(
            f"Running (framework={args.framework}, "
            f"harness={args.harness}, model={args.model})..."
        )
        runner = BenchmarkRunner(
            task, catalog, model=args.model, framework=args.framework,
            harness=args.harness, trace_writer=writer,
            max_turns=args.max_turns,
            workspace_manager=workspace_manager,
            agent_timeout=getattr(args, "agent_timeout", 300),
            judge_model=args.judge_model,
            skip_judge=args.skip_judge,
        )

        metrics = None
        violations: list = []
        trace = None

        try:
            trace = runner.run(run_id=run_id)

            print(f"  actions:    {trace.n_actions}")
            print(f"  tool_calls: {trace.n_tool_calls}")
            print(f"  comms:      {trace.n_communications}")
            if trace.error:
                print(f"  runner error: {trace.error}")
            print()

            print("Checking access rules...")
            scope_keys = {t.name: t.dedup_scope_keys for t in catalog.tools if t.dedup_scope_keys}
            decisions = check_trace(
                trace.actions,
                task.access_rules,
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
                print(f"  VIOLATION #{v.action_sequence_num}: {v.violation_code.value} "
                      f"({v.severity}) — {rule_desc}")

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
                )
            )

            metrics = compute_metrics(
                trace.run_id, trace.actions, decisions, trace.task_completed or False,
                tool_scope_keys=scope_keys,
                resource_tools={t.name for t in catalog.tools if t.is_resource},
                task_completion_score=trace.task_completion_score,
                operational_safety_score=operational_safety_score,
            )
            print("Metrics:")
            print("  Layer 1 — Protocol Integrity:")
            print(f"    protocol_safety_score: {metrics.protocol_safety_score:.2f}")
            print(f"    tool_penalty:          {metrics.tool_penalty:.2f}")
            print(f"    resource_penalty:      {metrics.resource_penalty:.2f}")
            print(f"    comm_penalty:          {metrics.comm_penalty:.2f}")
            op_gov = (
                f"{metrics.operational_safety_score:.2f}"
                if metrics.operational_safety_score is not None else "n/a"
            )
            print("  Layer 2 — Operational Governance:")
            print(f"    operational_safety:   {op_gov}")
            print("  Layer 3 — Decision Outcome:")
            print(f"    task_completion:      {metrics.task_completion_score:.2f}")
            if metrics.violations_by_code:
                print(f"  violations:        {metrics.violations_by_code}")
            print()

            writer.write_event(TraceEnd(
                trace_id=run_id,
                n_actions=trace.n_actions,
                n_tool_calls=trace.n_tool_calls,
                n_communications=trace.n_communications,
                n_violations=len(violations),
                protocol_safety_score=metrics.protocol_safety_score,
                tool_penalty=metrics.tool_penalty,
                resource_penalty=metrics.resource_penalty,
                comm_penalty=metrics.comm_penalty,
                operational_safety_score=metrics.operational_safety_score,
                task_completion_score=metrics.task_completion_score,
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
            canonical_trace_path = trace_path
            if trace is not None and args.framework == "clawteam":
                (
                    canonical_trace_path,
                    canonical_run_id,
                    canonical_llm,
                ) = canonicalize_clawteam_artifacts(
                    trace_path=trace_path,
                    run_id=trace.run_id,
                    harness=args.harness,
                    agent_roles=[a.role for a in task.agents],
                    results_dir=Path(args.output) if args.output else None,
                    task_id=task.task_id,
                    current_llm=trace.llm,
                )
                trace.run_id = canonical_run_id
                trace.llm = canonical_llm
                trace_path = canonical_trace_path
            print(f"Trace saved to {trace_path}")

            if args.output and trace is not None:
                out_dir = Path(args.output)
                out_dir.mkdir(parents=True, exist_ok=True)
                db_path = out_dir / f"{task.task_id}_{run_id}.sqlite"
                if runner.dump_bank_to_file(db_path):
                    if args.framework == "clawteam" and trace.run_id != run_id:
                        canonical_db_path = out_dir / f"{task.task_id}_{trace.run_id}.sqlite"
                        db_path.replace(canonical_db_path)
                        db_path = canonical_db_path
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
                "n_actions": trace.n_actions,
                "n_tool_calls": trace.n_tool_calls,
                "n_communications": trace.n_communications,
                "n_violations": len(violations),
                # Layer 1
                "protocol_safety_score": metrics.protocol_safety_score,
                "tool_penalty": metrics.tool_penalty,
                "resource_penalty": metrics.resource_penalty,
                "comm_penalty": metrics.comm_penalty,
                # Layer 2
                "operational_safety_score": metrics.operational_safety_score,
                # Layer 3
                "task_completion_score": metrics.task_completion_score,
                # Backward compat
                "violations_by_code": metrics.violations_by_code,
                "violations_by_severity": metrics.violations_by_severity,
                "trace_file": str(trace_path),
                "error": trace.error,
            }
            out_path = out_dir / f"{task.task_id}_{trace.run_id}.json"
            with open(out_path, "w") as f:
                json.dump(report, f, indent=2)
            print(f"Report saved to {out_path}")


def _load_trace_actions(
    trace_path: Path,
) -> tuple[list[ObservableAction], str, str, str, str]:
    """Reconstruct ObservableAction list from a trace JSONL file.

    Returns (actions, task_id, domain, run_id, final_output).
    """
    actions: list[ObservableAction] = []
    task_id = ""
    domain = ""
    run_id = ""
    final_output = ""

    with open(trace_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            etype = event.get("type", "")

            if etype == "trace_start":
                task_id = event.get("task_id", "")
                domain = event.get("domain", "")
                run_id = event.get("trace_id", "")

            elif etype == "tool_call":
                actions.append(ObservableAction(
                    sequence_num=event["sequence_num"],
                    run_id=run_id,
                    surface=ActionSurface.TOOL_CALL,
                    agent_id=event["agent_id"],
                    agent_role=event["agent_role"],
                    tool_name=event.get("tool_name"),
                    tool_args=event.get("tool_args"),
                    tool_result=event.get("tool_result"),
                ))

            elif etype == "communication":
                actions.append(ObservableAction(
                    sequence_num=event["sequence_num"],
                    run_id=run_id,
                    surface=ActionSurface.COMMUNICATION,
                    agent_id=event["agent_id"],
                    agent_role=event["agent_role"],
                    target_agent=event.get("target_agent"),
                    target_role=event.get("target_role"),
                    content=event.get("content"),
                ))
                if event.get("target_role") == "user":
                    final_output = event.get("content", "")

    return actions, task_id, domain, run_id, final_output


class _SqliteBank:
    """Read-only bank proxy for rejudge — wraps a saved SQLite file."""

    def __init__(self, path: Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row

    def query_sql(self, query: str) -> list[dict]:
        stripped = query.strip().lstrip("(")
        if not stripped.upper().startswith("SELECT"):
            raise ValueError("query_sql only allows SELECT")
        return [dict(row) for row in self.conn.execute(query).fetchall()]

    def close(self) -> None:
        self.conn.close()


def cmd_rejudge(args: argparse.Namespace) -> None:
    """Re-run judge scoring on an existing trace without re-running the agent."""
    pkg_root = Path(__file__).resolve().parent
    trace_path = Path(args.trace)

    print(f"Loading trace: {trace_path}")
    actions, task_id, domain, run_id, final_output = _load_trace_actions(trace_path)
    print(f"  task_id: {task_id}  run_id: {run_id}")
    print(f"  actions: {len(actions)}  "
          f"tool_calls: {sum(1 for a in actions if a.surface == 'tool_call')}  "
          f"comms: {sum(1 for a in actions if a.surface == 'communication')}")

    if not actions:
        print("  ERROR: no actions found in trace — skipping")
        sys.exit(1)

    # Resolve and load task YAML
    task_yaml = resolve_task_path(f"{task_id}.yaml", project_root=pkg_root, cwd=Path.cwd())
    task, catalog = load_task_with_tools(task_yaml)
    print()

    # Load bank from saved SQLite if available
    bank = None
    if args.output:
        out_dir = Path(args.output)
        bank_candidates = list(out_dir.glob(f"{task_id}_{run_id}.sqlite"))
        if bank_candidates:
            bank = _SqliteBank(bank_candidates[0])
            print(f"  Bank loaded from {bank_candidates[0]}")

    try:
        # Layer 1: Protocol Integrity (deterministic)
        print("Checking access rules...")
        scope_keys = {t.name: t.dedup_scope_keys for t in catalog.tools if t.dedup_scope_keys}
        decisions = check_trace(actions, task.access_rules, domain=task.domain)

        violations = [d for d in decisions if d.violated]
        for v in violations:
            rule_desc = v.rule.description if v.rule else ""
            print(f"  VIOLATION #{v.action_sequence_num}: {v.violation_code.value} "
                  f"({v.severity}) — {rule_desc}")
        if not violations:
            print("  No violations detected.")
        print()

        # Layer 2: Operational Governance (LLM judge)
        hub_role = task.metadata.get("hub_role")
        if not isinstance(hub_role, str):
            hub_role = task.agents[0].role
        operational_safety_score = asyncio.run(
            evaluate_operational_governance(
                actions,
                task.ground_truth_tool_paths or {},
                hub_role,
                access_rules=task.access_rules,
                model=args.judge_model,
                skip=args.skip_judge,
            )
        )

        # Layer 3: Completion (hybrid rule + LLM)
        completion = asyncio.run(
            evaluate_completion_checkpoints(
                task, actions, final_output,
                bank=bank,
                judge_model=args.judge_model,
                skip_judge=args.skip_judge,
            )
        )

        # Combine metrics
        metrics = compute_metrics(
            run_id, actions, decisions, completion.task_completed,
            tool_scope_keys=scope_keys,
            resource_tools={t.name for t in catalog.tools if t.is_resource},
            task_completion_score=completion.task_completion_score,
            operational_safety_score=operational_safety_score,
        )

        print("Metrics:")
        print("  Layer 1 — Protocol Integrity:")
        print(f"    protocol_safety_score: {metrics.protocol_safety_score:.2f}")
        print(f"    tool_penalty:          {metrics.tool_penalty:.2f}")
        print(f"    resource_penalty:      {metrics.resource_penalty:.2f}")
        print(f"    comm_penalty:          {metrics.comm_penalty:.2f}")
        op_gov = (
            f"{metrics.operational_safety_score:.2f}"
            if metrics.operational_safety_score is not None else "n/a"
        )
        print("  Layer 2 — Operational Governance:")
        print(f"    operational_safety:   {op_gov}")
        print("  Layer 3 — Decision Outcome:")
        print(f"    task_completion:      {metrics.task_completion_score:.2f}")
        if metrics.violations_by_code:
            print(f"  violations:        {metrics.violations_by_code}")
        print()

        # Update trace JSONL: replace the old error trace_end with scored one
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", dir=trace_path.parent, suffix=".jsonl", delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            with open(trace_path) as src:
                for line in src:
                    evt = json.loads(line.strip())
                    if evt.get("type") == "trace_end":
                        continue  # drop old trace_end
                    tmp.write(line)
            # Write new trace_end with real scores
            for d in decisions:
                tmp.write(json.dumps({
                    "type": "access_decision",
                    "trace_id": run_id,
                    "action_sequence_num": d.action_sequence_num or 0,
                    "violated": d.violated,
                    "violation_code": d.violation_code.value if d.violation_code else None,
                    "severity": d.severity,
                    "rule_description": d.rule.description if d.rule else None,
                }) + "\n")
            tmp.write(json.dumps({
                "type": "trace_end",
                "trace_id": run_id,
                "n_actions": len(actions),
                "n_tool_calls": sum(1 for a in actions if a.surface == "tool_call"),
                "n_communications": sum(1 for a in actions if a.surface == "communication"),
                "n_violations": len(violations),
                "protocol_safety_score": metrics.protocol_safety_score,
                "tool_penalty": metrics.tool_penalty,
                "resource_penalty": metrics.resource_penalty,
                "comm_penalty": metrics.comm_penalty,
                "operational_safety_score": metrics.operational_safety_score,
                "task_completion_score": metrics.task_completion_score,
                "violations_by_code": metrics.violations_by_code,
                "wall_time_s": 0.0,
                "error": None,
            }) + "\n")
        tmp_path.replace(trace_path)
        print(f"Trace updated: {trace_path}")

        # Save report
        if args.output:
            out_dir = Path(args.output)
            out_dir.mkdir(parents=True, exist_ok=True)
            report = {
                "task_id": task.task_id,
                "run_id": run_id,
                "framework": "clawteam",
                "llm": run_id.split("-")[1] if "-" in run_id else "",
                "n_actions": len(actions),
                "n_tool_calls": sum(1 for a in actions if a.surface == "tool_call"),
                "n_communications": sum(1 for a in actions if a.surface == "communication"),
                "n_violations": len(violations),
                "protocol_safety_score": metrics.protocol_safety_score,
                "tool_penalty": metrics.tool_penalty,
                "resource_penalty": metrics.resource_penalty,
                "comm_penalty": metrics.comm_penalty,
                "operational_safety_score": metrics.operational_safety_score,
                "task_completion_score": metrics.task_completion_score,
                "violations_by_code": metrics.violations_by_code,
                "violations_by_severity": metrics.violations_by_severity,
                "trace_file": str(trace_path),
                "error": None,
            }
            out_path = out_dir / f"{task_id}_{run_id}.json"
            with open(out_path, "w") as f:
                json.dump(report, f, indent=2)
            print(f"Report saved to {out_path}")

    finally:
        if bank is not None:
            bank.close()


def main() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(env_path)

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
    run_p.add_argument(
        "--model",
        default="claude-sonnet-4",
        help="LLM model label for trace metadata (default: claude-sonnet-4)",
    )
    run_p.add_argument(
        "--framework",
        default="clawteam",
        choices=["clawteam", "oai"],
        help="MAS framework adapter (default: clawteam)",
    )
    run_p.add_argument(
        "--harness",
        default="claude",
        choices=["claude", "codex", "openclaw"],
        help="Agent harness for ClawTeam adapter (default: claude)",
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
        default="gpt-5.4-xhigh",
        help="GPT model for completion and operational judges (default: gpt-5.4-xhigh)",
    )
    run_p.add_argument("--output", "-o", help="Output directory for results JSON")
    _default_trace = str(Path(__file__).resolve().parent / "traces")
    run_p.add_argument("--trace-dir", default=_default_trace,
                       help="Directory for JSONL trace files")
    run_p.add_argument("--max-turns", type=int, default=30,
                       help="Maximum framework turns per run (default: 30)")
    run_p.add_argument("--repeat", type=int, default=1,
                       help="Repeat the same task sequentially (default: 1)")

    # --- rejudge subcommand ---
    rj_p = sub.add_parser("rejudge", help="Re-run judge scoring on an existing trace")
    rj_p.add_argument("trace", help="Path to existing trace JSONL file")
    rj_p.add_argument("--judge-model", default="gpt-5.4-xhigh",
                       help="GPT model for judges (default: gpt-5.4-xhigh)")
    rj_p.add_argument("--skip-judge", action="store_true",
                       help="Skip LLM judge; deterministic checkpoints only")
    rj_p.add_argument("--output", "-o", help="Output directory for results JSON")

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "rejudge":
        cmd_rejudge(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
