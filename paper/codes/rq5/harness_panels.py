from __future__ import annotations

import math
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

CODES_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

sys.path.insert(0, str(CODES_ROOT))

from paper_style import (
    EDGE,
    SHOW_FIGURE_LEGENDS,
    apply_paper_style,
    style_axes,
)


apply_paper_style()

import rq1_common  # noqa: E402


COMPARISONS = [
    {
        "row_label": "GPT-5.4:\nOpenClaw - Codex",
        "openclaw": ("openclaw", "gpt-5.4"),
        "native": ("codex", "gpt-5.4"),
        "sample_n": 100,
    },
    {
        "row_label": "Claude Sonnet:\nOpenClaw - Claude Code",
        "openclaw": ("openclaw", "claude-sonnet-4-6"),
        "native": ("claude", "claude-sonnet-4-6"),
        "sample_n": 100,
    },
    {
        "row_label": "Claude Opus:\nOpenClaw - Claude Code",
        "openclaw": ("openclaw", "claude-opus-4-6"),
        "native": ("claude", "claude-opus-4-6"),
        "sample_n": None,
    },
]

FOREST_METRICS = [
    {
        "key": "sar_mean",
        "label": r"$\Delta$SAR$_{\mathrm{mean}}$",
        "color": "#3B6FA8",
        "edge": EDGE,
        "offset": 0.18,
        "scale": 1.0,
    },
    {
        "key": "tcr",
        "label": r"$\Delta$Task Completion",
        "color": "#4F9D5A",
        "edge": EDGE,
        "offset": 0.00,
        "scale": 1.0,
    },
    {
        "key": "n_actions",
        "label": r"$\Delta$Number of Actions / 100",
        "color": "#D65D6E",
        "edge": EDGE,
        "offset": -0.18,
        "scale": 100.0,
    },
]

OAI_SAMPLE_TASKS = [
    "dl-t11",
    "dl-t13",
    "ec-t10",
    "ec-t1",
    "ec-t12",
    "fin-t16",
    "fin-t10",
    "fin-t11",
    "fin-t5",
    "hc-t1",
    "lc-t9",
    "ec-t22",
    "ec-t28",
    "ec-t39",
    "off-t12",
    "off-t16",
    "off-t3",
    "off-t10",
    "si-t7",
    "si-t3",
]

PANEL_B_SYSTEMS = [
    {
        "label": "OpenClaw",
        "harness": "openclaw",
        "model": "gpt-5.4",
        "color": "#9BCFE6",
    },
    {
        "label": "OpenAI Agents SDK",
        "harness": "oai",
        "model": "gpt-5.4",
        "color": "#C0BADE",
    },
    {
        "label": "Google ADK",
        "harness": "adk",
        "model": "gpt-5.4",
        "color": "#C4ECC1",
    },
]


def latest_rows_by_task(rows: list[dict], harness: str, model: str) -> dict[str, dict]:
    def row_mtime(row: dict) -> float:
        trace = row.get("trace")
        if hasattr(trace, "stat"):
            return float(trace.stat().st_mtime)
        return float(row.get("mtime") or 0.0)

    out: dict[str, dict] = {}
    for row in rows:
        if row["harness"] != harness or row["model"] != model:
            continue
        task_id = row.get("task_id")
        if not task_id:
            continue
        previous = out.get(task_id)
        if previous is None or row_mtime(row) > row_mtime(previous):
            out[task_id] = row
    return out


def bootstrap_ci(values: np.ndarray, seed: int = 13, n_boot: int = 5000) -> tuple[float, float, float]:
    values = np.asarray(values, dtype=float)
    mean = float(np.mean(values))
    if len(values) <= 1:
        return mean, mean, mean
    rng = np.random.default_rng(seed)
    samples = rng.choice(values, size=(n_boot, len(values)), replace=True)
    boot_means = samples.mean(axis=1)
    lo, hi = np.percentile(boot_means, [2.5, 97.5])
    return mean, float(lo), float(hi)


def channel_sar_scores(row: dict) -> dict[str, float]:
    existing = {
        "sar_tool": row.get("sar_tool"),
        "sar_rsrc": row.get("sar_rsrc"),
        "sar_flow": row.get("sar_flow"),
    }
    if all(value is not None and not math.isnan(float(value)) for value in existing.values()):
        return {key: float(value) for key, value in existing.items()}

    trace_row = rq1_common.sar_final.per_task_counts(row["trace"])
    return {
        "sar_tool": float(rq1_common.sar_final._penalty_score(trace_row["counts"]["tool"])),
        "sar_rsrc": float(rq1_common.sar_final._penalty_score(trace_row["counts"]["rsrc"])),
        "sar_flow": float(rq1_common.sar_final._penalty_score(trace_row["counts"]["flow"])),
    }


def compute_forest_rows(base_rows: list[dict]) -> list[dict]:
    forest: list[dict] = []

    for comp_idx, comparison in enumerate(COMPARISONS):
        open_harness, open_model = comparison["openclaw"]
        native_harness, native_model = comparison["native"]
        open_rows = latest_rows_by_task(base_rows, open_harness, open_model)
        native_rows = latest_rows_by_task(base_rows, native_harness, native_model)
        matched_tasks = sorted(set(open_rows) & set(native_rows))
        full_n = len(matched_tasks)
        sample_n = comparison.get("sample_n")
        if sample_n is not None and len(matched_tasks) > sample_n:
            rng = np.random.default_rng(20260502 + comp_idx)
            matched_tasks = sorted(
                rng.choice(matched_tasks, size=sample_n, replace=False).tolist()
            )

        metric_rows = []
        for metric_idx, metric in enumerate(FOREST_METRICS):
            diffs = np.array(
                [
                    (float(open_rows[task][metric["key"]]) - float(native_rows[task][metric["key"]]))
                    / metric["scale"]
                    for task in matched_tasks
                ],
                dtype=float,
            )
            mean, lo, hi = bootstrap_ci(diffs, seed=171 + comp_idx * 17 + metric_idx)
            metric_rows.append(
                {
                    "metric": metric,
                    "mean": mean,
                    "lo": lo,
                    "hi": hi,
                    "n": len(matched_tasks),
                }
            )

        forest.append(
            {
                "label": comparison["row_label"],
                "metrics": metric_rows,
                "n": len(matched_tasks),
                "full_n": full_n,
            }
        )

    return forest


def compute_panel_b(base_rows: list[dict]) -> list[dict]:
    system_maps = [
        (system, latest_rows_by_task(base_rows, system["harness"], system["model"]))
        for system in PANEL_B_SYSTEMS
    ]
    common_tasks = [
        task
        for task in OAI_SAMPLE_TASKS
        if all(task in by_task for _system, by_task in system_maps)
    ]

    panel_rows: list[dict] = []
    for system, by_task in system_maps:
        selected = [by_task[task] for task in common_tasks]
        if selected:
            channel_rows = [channel_sar_scores(row) for row in selected]
            sar_tool = float(np.mean([row["sar_tool"] for row in channel_rows]))
            sar_rsrc = float(np.mean([row["sar_rsrc"] for row in channel_rows]))
            sar_flow = float(np.mean([row["sar_flow"] for row in channel_rows]))
        else:
            sar_tool = float("nan")
            sar_rsrc = float("nan")
            sar_flow = float("nan")
        panel_rows.append(
            {
                **system,
                "sar_tool": sar_tool,
                "sar_rsrc": sar_rsrc,
                "sar_flow": sar_flow,
                "n": len(selected),
                "matched_tasks": common_tasks,
                "missing": [task for task in OAI_SAMPLE_TASKS if task not in by_task],
            }
        )
    return panel_rows


def draw_panel_a(ax: plt.Axes, forest_rows: list[dict]) -> None:
    y_base = np.arange(len(forest_rows))[::-1].astype(float)
    # Row tick spacing is 1.0; half-span must be 0.5 so adjacent axhspan bands meet (no white gutters).
    row_stripe_half = 0.5

    for row_idx, row in enumerate(forest_rows):
        y = y_base[row_idx]
        ax.axhspan(
            y - row_stripe_half,
            y + row_stripe_half,
            color="#F7F9FB" if row_idx % 2 == 0 else "#FFFFFF",
            zorder=0,
        )
        for item in row["metrics"]:
            metric = item["metric"]
            x = item["mean"]
            xerr = np.array([[x - item["lo"]], [item["hi"] - x]], dtype=float)
            ax.errorbar(
                x,
                y + metric["offset"],
                xerr=xerr,
                fmt="o",
                markersize=8.0,
                markerfacecolor=metric["color"],
                markeredgecolor="white",
                markeredgewidth=1.15,
                ecolor=metric["color"],
                elinewidth=2.75,
                capsize=5.2,
                capthick=2.35,
                alpha=0.96,
                zorder=4,
            )

    ax.axvline(0, color=EDGE, linewidth=1.55, linestyle=(0, (3, 3)), alpha=0.88, zorder=2)
    ax.text(
        0.006,
        y_base.max() + 0.47,
        "no harness effect",
        ha="left",
        va="center",
        fontsize=8.0,
        color=EDGE,
    )

    ax.set_yticks(y_base)
    ax.set_yticklabels([row["label"] for row in forest_rows], fontsize=8.8)
    ax.set_xlabel("Mean Difference, Δ (OpenClaw - Native Harness)")
    ax.set_title("Matched Task Deltas", pad=8)

    ax.set_xlim(-0.48, 0.30)
    ax.set_ylim(-0.55, y_base.max() + 0.60)
    ax.set_xticks(np.arange(-0.4, 0.31, 0.1))
    style_axes(ax, grid_axis="y", full_frame=True)
    ax.tick_params(axis="y", length=0)

    if SHOW_FIGURE_LEGENDS:
        handles = [
            plt.Line2D(
                [0],
                [0],
                marker="o",
                color="none",
                markerfacecolor=metric["color"],
                markeredgecolor="white",
                markersize=6.8,
                label=metric["label"],
            )
            for metric in FOREST_METRICS
        ]
        ax.legend(
            handles=handles,
            loc="upper left",
            bbox_to_anchor=(0.01, 0.985),
            frameon=False,
            fontsize=7.6,
            ncol=1,
            handletextpad=0.5,
        )


def draw_panel_b(ax: plt.Axes, panel_rows: list[dict]) -> None:
    metrics = [
        ("sar_tool", r"SAR$^{t}$"),
        ("sar_rsrc", r"SAR$^{r}$"),
        ("sar_flow", r"SAR$^{f}$"),
    ]
    x = np.arange(len(metrics), dtype=float)
    width = min(0.24, 0.78 / max(len(panel_rows), 1))
    offsets = (np.arange(len(panel_rows), dtype=float) - (len(panel_rows) - 1) / 2) * width

    for idx, row in enumerate(panel_rows):
        values = [row[key] for key, _label in metrics]
        ax.bar(
            x + offsets[idx],
            values,
            width=width,
            color=row["color"],
            edgecolor="none",
            linewidth=0,
            label=f"{row['label']} (n={row['n']})",
            alpha=0.97,
            zorder=3,
        )
        for xpos, value in zip(x + offsets[idx], values):
            if np.isnan(value):
                ax.text(xpos, 0.05, "missing", ha="center", va="bottom", rotation=90, fontsize=7.0, color="#6B7280")

    ax.set_xticks(x)
    ax.set_xticklabels([label for _key, label in metrics], fontsize=9.1)
    ax.set_ylim(0, 1.02)
    ax.set_ylabel("Mean SAR score on five matched tasks")
    ax.set_title("Boundary Channel Comparison Sample", pad=8)
    style_axes(ax, grid_axis="y", full_frame=True)
    if SHOW_FIGURE_LEGENDS:
        ax.legend(
            loc="upper left",
            bbox_to_anchor=(1.02, 1.0),
            ncol=1,
            frameon=True,
            framealpha=0.96,
            facecolor="white",
            edgecolor="#CCCCCC",
            fontsize=8.0,
        )
    ax.tick_params(axis="x", length=0)

    missing_parts = [
        f"{row['label']}={len(row['missing'])}"
        for row in panel_rows
        if row["missing"]
    ]
    if missing_parts:
        ax.text(
            0.5,
            -0.12,
            f"Matched tasks: n={panel_rows[0]['n'] if panel_rows else 0}; missing counts: "
            + ", ".join(missing_parts),
            ha="center",
            va="top",
            transform=ax.transAxes,
            fontsize=7.0,
            color="#6B7280",
        )
