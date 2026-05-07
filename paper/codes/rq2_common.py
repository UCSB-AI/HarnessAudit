from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_ROOT = REPO_ROOT / "masp"
TRACES_ROOT = PKG_ROOT / "traces"
RESULTS_ROOT = PKG_ROOT / "results"
# Same default output dir as rq1_common (paper/codes/images).
IMAGES_DIR = Path(__file__).resolve().parent / "images"

sys.path.insert(0, str(REPO_ROOT))
import sar_final  # noqa: E402


GROUP_ORDER = [
    ("openclaw", "gpt-5.4"),
    ("openclaw", "claude-opus-4-6"),
    ("openclaw", "claude-sonnet-4-6"),
    ("openclaw", "gemini-3.1-pro-preview"),
    ("openclaw", "glm-5v-turbo"),
    ("openclaw", "kimi-k2.6"),
    ("openclaw", "qwen3.5-plus"),
    ("claude", "claude-opus-4-6"),
    ("claude", "claude-sonnet-4-6"),
    ("codex", "gpt-5.4"),
]

GROUP_LABELS = {
    ("openclaw", "gpt-5.4"): "OpenClaw\nChatGPT-5.4",
    ("openclaw", "claude-opus-4-6"): "OpenClaw\nClaude Opus 4.6",
    ("openclaw", "claude-sonnet-4-6"): "OpenClaw\nClaude Sonnet 4.6",
    ("openclaw", "gemini-3.1-pro-preview"): "OpenClaw\nGemini 3.1 Pro",
    ("openclaw", "glm-5v-turbo"): "OpenClaw\nGLM 5V Turbo",
    ("openclaw", "kimi-k2.6"): "OpenClaw\nKimi K2.6",
    ("openclaw", "qwen3.5-plus"): "OpenClaw\nQwen 3.5 Plus",
    ("claude", "claude-opus-4-6"): "Claude Code\nClaude Opus 4.6",
    ("claude", "claude-sonnet-4-6"): "Claude Code\nClaude Sonnet 4.6",
    ("codex", "gpt-5.4"): "Codex\nChatGPT-5.4",
}


DOMAIN_ORDER = [
    "finance",
    "ecommerce",
    "healthcare",
    "office",
    "daily_life",
    "legal_compliance",
    "social_interaction",
    "software_engineering",
]

DOMAIN_LABELS = {
    "finance": "Finance",
    "ecommerce": "E-commerce",
    "healthcare": "Healthcare",
    "office": "Office",
    "daily_life": "Daily life",
    "legal_compliance": "Legal compliance",
    "social_interaction": "Social interaction",
    "software_engineering": "Software engineering",
}


def channel_penalty_score(counts: dict[str, int]) -> float:
    return sar_final._penalty_score(counts)


def channel_penalty_scores(counts: dict[str, dict[str, int]]) -> dict[str, float]:
    return {
        channel: channel_penalty_score(counts[channel])
        for channel in sar_final.CHANNELS
    }


def sar_mean_from_counts(counts: dict[str, dict[str, int]]) -> float:
    return float(np.mean(list(channel_penalty_scores(counts).values())))


def trace_agent_violation_stats(trace_path: Path) -> dict[str, float | int]:
    agents: set[str] = set()
    action_agents: dict[int, str] = {}
    violation_sequences: list[int | None] = []

    with trace_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            event_type = event.get("type", "")

            if event_type == "trace_start":
                for agent in event.get("agents") or []:
                    agent_key = agent.get("agent_id") or agent.get("role")
                    if agent_key:
                        agents.add(agent_key)
                continue

            if event_type in {"tool_call", "communication"}:
                seq = event.get("sequence_num")
                agent_key = event.get("agent_id") or event.get("agent_role")
                if isinstance(seq, int) and agent_key:
                    action_agents[seq] = agent_key
                    agents.add(agent_key)
                continue

            if event_type == "access_decision" and event.get("violated"):
                violation_sequences.append(event.get("action_sequence_num"))

    violations_by_agent: dict[str, int] = {agent: 0 for agent in agents}
    unattributed = 0
    for seq in violation_sequences:
        agent = action_agents.get(seq) if isinstance(seq, int) else None
        if agent:
            agents.add(agent)
            violations_by_agent.setdefault(agent, 0)
            violations_by_agent[agent] += 1
        else:
            unattributed += 1

    agent_count = len(agents)
    violation_total = sum(violations_by_agent.values()) + unattributed
    return {
        "agent_count": agent_count,
        "violation_total": violation_total,
        "violations_per_agent": (
            violation_total / agent_count if agent_count else 0.0
        ),
    }


def parse_trace_context(trace_path: Path) -> tuple[str, str, list[dict]]:
    task_id = ""
    domain = ""
    comms: list[dict] = []
    with trace_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            event_type = event.get("type", "")
            if event_type == "trace_start":
                task_id = event.get("task_id", "")
                domain = event.get("domain", "")
            elif event_type == "communication":
                comms.append(
                    {
                        "kind": "comm",
                        "agent_role": event.get("agent_role", ""),
                        "target_role": event.get("target_role", ""),
                        "content": event.get("content", ""),
                    }
                )
    return task_id, domain, comms


def flow_violation_split(task_id: str, domain: str, comms: list[dict]) -> dict[str, int]:
    out = {"comm": 0, "data": 0}
    if not task_id:
        return out

    rules = [
        rule
        for rule in sar_final._load_info_rules(task_id)
        if sar_final._rule_is_cleaned_flow_rule(rule)
        and sar_final._rule_is_fireable(rule, comms)
    ]

    for comm in comms:
        comm_severities = {
            rule.severity
            for rule in rules
            if rule.access_type == "information.communication"
            and sar_final._communication_matches_info_rule(comm, rule, domain)
        }
        data_severities = {
            rule.severity
            for rule in rules
            if rule.access_type == "information.data_leak"
            and sar_final._communication_matches_info_rule(comm, rule, domain)
        }
        if comm_severities:
            out["comm"] += 1
        if data_severities:
            out["data"] += 1
    return out


def iter_base_trace_result_pairs():
    for trace_path in sorted(TRACES_ROOT.glob("*/*/*.jsonl")):
        rel = trace_path.relative_to(TRACES_ROOT)
        if rel.parts[0] == "perturbations":
            continue
        result_path = RESULTS_ROOT / rel.with_suffix(".json")
        if result_path.is_file():
            yield trace_path, result_path, rel.parts[0], rel.parts[1]


def load_base_runs() -> list[dict]:
    rows: list[dict] = []
    for trace_path, result_path, harness, model in iter_base_trace_result_pairs():
        try:
            trace_row = sar_final.per_task_counts(trace_path)
            if trace_row.get("n_actions", 0) <= sar_final.DIRTY_ACTION_THRESHOLD:
                continue
            result = json.loads(result_path.read_text())
            task_id, domain, comms = parse_trace_context(trace_path)
            agent_stats = trace_agent_violation_stats(trace_path)
            flow_split = flow_violation_split(task_id, domain, comms)
        except Exception:
            continue

        tcr = result.get("task_completion_score")
        if tcr is None:
            continue

        channel_scores = channel_penalty_scores(trace_row["counts"])
        rows.append(
            {
                "harness": harness,
                "model": model,
                "group": (harness, model),
                "task_id": task_id or trace_row.get("task_id") or result.get("task_id"),
                "domain": domain or trace_row.get("domain"),
                "trace": trace_path,
                "result": result_path,
                "counts": trace_row["counts"],
                "sar_tool": channel_scores["tool"],
                "sar_rsrc": channel_scores["rsrc"],
                "sar_flow": channel_scores["flow"],
                "sar_mean": float(np.mean(list(channel_scores.values()))),
                "tcr": float(tcr),
                "agent_count": agent_stats["agent_count"],
                "violation_total": agent_stats["violation_total"],
                "violations_per_agent": agent_stats["violations_per_agent"],
                "tool_v": trace_row["counts"]["tool"]["V_low"] + trace_row["counts"]["tool"]["V_high"],
                "rsrc_v": trace_row["counts"]["rsrc"]["V_low"] + trace_row["counts"]["rsrc"]["V_high"],
                "comm_v": flow_split["comm"],
                "data_v": flow_split["data"],
                "mtime": trace_path.stat().st_mtime,
            }
        )
    return rows


def rows_by_group(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["group"]].append(row)
    return grouped


def protocol_heatmap(rows: list[dict]) -> tuple[list[str], np.ndarray]:
    grouped = rows_by_group(rows)
    labels: list[str] = []
    values: list[list[float]] = []
    for group in GROUP_ORDER:
        group_rows = grouped.get(group, [])
        if not group_rows:
            continue
        metrics = sar_final.compute_metrics(group_rows)
        labels.append(GROUP_LABELS[group])
        values.append(
            [
                metrics["penalty"]["tool"],
                metrics["penalty"]["rsrc"],
                metrics["penalty"]["flow"],
            ]
        )
    return labels, np.array(values, dtype=float)


def violation_composition(rows: list[dict]) -> tuple[list[str], np.ndarray]:
    grouped = rows_by_group(rows)
    labels: list[str] = []
    values: list[list[float]] = []
    for group in GROUP_ORDER:
        group_rows = grouped.get(group, [])
        if not group_rows:
            continue
        totals = np.array(
            [
                sum(row["tool_v"] for row in group_rows),
                sum(row["rsrc_v"] for row in group_rows),
                sum(row["comm_v"] for row in group_rows),
                sum(row["data_v"] for row in group_rows),
            ],
            dtype=float,
        )
        denom = totals.sum()
        labels.append(GROUP_LABELS[group])
        values.append((totals / denom).tolist() if denom > 0 else [0.0, 0.0, 0.0, 0.0])
    return labels, np.array(values, dtype=float)


def agent_violation_totals(rows: list[dict]) -> tuple[list[str], np.ndarray]:
    grouped = rows_by_group(rows)
    labels: list[str] = []
    values: list[float] = []
    for group in GROUP_ORDER:
        group_rows = grouped.get(group, [])
        if not group_rows:
            continue
        total_agents = sum(int(row.get("agent_count") or 0) for row in group_rows)
        total_violations = sum(int(row.get("violation_total") or 0) for row in group_rows)
        labels.append(GROUP_LABELS[group])
        values.append(total_violations / total_agents if total_agents else 0.0)
    return labels, np.array(values, dtype=float)


def domain_metrics(rows: list[dict]) -> tuple[list[str], np.ndarray, np.ndarray]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("domain") and (row.get("harness"), row.get("model")) in GROUP_ORDER:
            grouped[row["domain"]].append(row)

    labels: list[str] = []
    sar_values: list[float] = []
    tcr_values: list[float] = []
    for domain in DOMAIN_ORDER:
        domain_rows = grouped.get(domain, [])
        if not domain_rows:
            continue
        metrics = sar_final.compute_metrics(domain_rows)
        labels.append(DOMAIN_LABELS.get(domain, domain))
        sar_values.append(float(metrics["penalty"]["mean"]))
        tcr_values.append(float(np.mean([row["tcr"] for row in domain_rows])))
    return labels, np.array(sar_values), np.array(tcr_values)


def domain_channel_sar(rows: list[dict]) -> tuple[list[str], np.ndarray]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("domain") and (row.get("harness"), row.get("model")) in GROUP_ORDER:
            grouped[row["domain"]].append(row)

    labels: list[str] = []
    values: list[list[float]] = []
    for domain in DOMAIN_ORDER:
        domain_rows = grouped.get(domain, [])
        if not domain_rows:
            continue
        metrics = sar_final.compute_metrics(domain_rows)
        penalty = metrics["penalty"]
        labels.append(DOMAIN_LABELS.get(domain, domain))
        values.append(
            [
                float(penalty["tool"]),
                float(penalty["rsrc"]),
                float(penalty["flow"]),
            ]
        )
    return labels, np.array(values, dtype=float)
