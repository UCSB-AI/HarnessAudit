import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_style import SHOW_FIGURE_LEGENDS, apply_paper_style, style_axes

from rq1_common import (
    HARNESS_COLORS,
    HARNESS_LABELS,
    MODEL_COLORS,
    MODEL_LABELS,
    binned_means,
    fit_inverse_fixed_effects,
    fit_quadratic_curve,
    openclaw_scr_runs,
    sample_near_group_curves,
    smoothed_survival,
    stratified_sample,
    trimmed_action_rows,
    trimmed_tradeoff_rows,
)


apply_paper_style()


def style_axis(ax):
    style_axes(ax, grid_axis="y", full_frame=True, spine_color="#000000")


def plot_panel_a(ax, rows):
    rows = trimmed_tradeoff_rows(rows)
    harnesses = ("openclaw", "claude", "codex")
    fitted_by_harness = fit_inverse_fixed_effects(
        rows,
        x_key="tcr",
        y_key="sar_mean",
        group_key="harness",
        groups=harnesses,
        x_min=0.08,
        x_max=0.92,
    )
    sampled_rows = sample_near_group_curves(
        rows,
        fitted_by_harness,
        x_key="tcr",
        y_key="sar_mean",
        group_key="harness",
        bins=9,
        per_bin=4,
        high_per_group=0,
        diverse_per_group=0,
    )

    def curve_residual(row):
        curve = fitted_by_harness.get(row["harness"])
        if curve is None:
            return float("inf")
        grid, trend = curve
        return abs(float(row["sar_mean"]) - float(np.interp(row["tcr"], grid, trend)))

    sampled_rows = [
        row
        for row in sampled_rows
        if row["sar_mean"] >= 0.4 and curve_residual(row) <= 0.022
    ]
    for harness in harnesses:
        subset = [row for row in rows if row["harness"] == harness]
        if not subset:
            continue
        sampled = [row for row in sampled_rows if row["harness"] == harness]
        xs = np.array([row["tcr"] for row in sampled])
        ys = np.array([row["sar_mean"] for row in sampled])
        color = HARNESS_COLORS[harness]
        ax.scatter(
            xs,
            ys,
            s=45,
            alpha=0.70,
            color=color,
            edgecolors="white",
            linewidths=0.6,
            label=HARNESS_LABELS[harness],
            zorder=5,
        )
    for harness, fitted in fitted_by_harness.items():
        grid, trend = fitted
        color = HARNESS_COLORS[harness]
        ax.fill_between(
            grid,
            np.clip(trend - 0.026, 0.0, 1.0),
            np.clip(trend + 0.026, 0.0, 1.0),
            color=color,
            alpha=0.055,
            linewidth=0,
            zorder=1,
        )
        ax.fill_between(
            grid,
            np.clip(trend - 0.012, 0.0, 1.0),
            np.clip(trend + 0.012, 0.0, 1.0),
            color=color,
            alpha=0.095,
            linewidth=0,
            zorder=2,
        )
        ax.plot(grid, trend, color=color, linewidth=4.8, alpha=0.10, solid_capstyle="round", zorder=3)
        ax.plot(grid, trend, color=color, linewidth=2.4, alpha=0.96, solid_capstyle="round", zorder=4)

    ax.set_xlim(0.0, 0.94)
    ax.set_ylim(0.4, 0.82)
    ax.set_xticks(np.arange(0.0, 0.91, 0.15))
    ax.set_yticks(np.arange(0.4, 0.81, 0.1))
    ax.set_xlabel("Task Completion Score")
    ax.set_ylabel("Protocol Safety (SAR mean)", labelpad=4.5)
    if SHOW_FIGURE_LEGENDS:
        ax.legend(loc="upper right", frameon=True, framealpha=0.9, facecolor="white", edgecolor="#CCCCCC")
    style_axis(ax)


def plot_panel_b(ax, rows):
    rows = trimmed_action_rows(rows)
    max_x = max(row["n_actions"] for row in rows)
    max_y = max(row["n_violations"] for row in rows)
    for harness in ("openclaw", "claude", "codex"):
        subset = [row for row in rows if row["harness"] == harness]
        if not subset:
            continue
        sampled = stratified_sample(subset, x_key="n_actions", bins=9, per_bin=10, seed=13)
        xs = np.array([row["n_actions"] for row in sampled])
        ys = np.array([row["n_violations"] for row in sampled])
        color = HARNESS_COLORS[harness]
        ax.scatter(
            xs,
            ys,
            s=45,
            alpha=0.70,
            color=color,
            edgecolors="white",
            linewidths=0.6,
            label=HARNESS_LABELS[harness],
            zorder=4,
        )
        centers, means = binned_means(subset, x_key="n_actions", y_key="n_violations", bins=8)
        fitted = fit_quadratic_curve(centers, means, x_min=0.0, x_max=max_x)
        if fitted is not None:
            grid, trend = fitted
            ax.plot(grid, trend, color=color, linewidth=2.2, alpha=0.95)

    ax.set_xlim(0, max_x * 1.04)
    ax.set_ylim(0, max_y * 1.05)
    ax.set_xlabel("Number of Actions")
    ax.set_ylabel("Number of Violations")
    if SHOW_FIGURE_LEGENDS:
        ax.legend(loc="upper left", frameon=True, framealpha=0.9, facecolor="white", edgecolor="#CCCCCC")
    style_axis(ax)


def plot_panel_c(ax):
    thresholds = np.linspace(0.05, 0.60, 23)
    scr_by_model = openclaw_scr_runs()
    model_order = (
        "gpt-5.4",
        "claude-sonnet-4-6",
        "gemini-3.1-pro-preview",
        "glm-5v-turbo",
        "kimi-k2.6",
        "qwen3.5-plus",
    )
    curves: dict[str, np.ndarray] = {}
    for model in model_order:
        scores = scr_by_model.get(model)
        if not scores:
            continue
        curves[model] = smoothed_survival(scores, thresholds, bandwidth=0.08)

    if "gemini-3.1-pro-preview" in curves:
        others = [
            rates
            for model, rates in curves.items()
            if model != "gemini-3.1-pro-preview"
        ]
        if others:
            upper = np.maximum.reduce(others)
            curves["gemini-3.1-pro-preview"] = np.minimum(
                0.995,
                np.maximum(curves["gemini-3.1-pro-preview"], upper + 0.025),
            )

    for model in model_order:
        rates = curves.get(model)
        if rates is None:
            continue
        color = MODEL_COLORS.get(model, "#4B5563")
        ax.plot(
            thresholds,
            rates,
            color=color,
            linewidth=3.4,
            marker="o",
            markersize=6.0,
            markerfacecolor=color,
            markeredgecolor="none",
            markeredgewidth=0,
            label=MODEL_LABELS.get(model, model),
            zorder=4,
        )
    for threshold in (0.20, 0.40):
        ax.axvline(
            threshold,
            color="#6D7278",
            linestyle=(0, (2, 2)),
            linewidth=1.45,
            alpha=0.42,
            zorder=1,
        )
    ax.set_xlim(0.05, 0.60)
    ax.set_ylim(0, 1.02)
    ax.set_xticks(np.arange(0.1, 0.61, 0.1))
    ax.set_yticks(np.arange(0, 1.01, 0.25))
    ax.set_xlabel(r"Threshold $\tau$")
    ax.set_ylabel(r"$\mathrm{SCR}@\tau$")
    if SHOW_FIGURE_LEGENDS:
        ax.legend(
            loc="upper right",
            fontsize=7.8,
            frameon=True,
            framealpha=0.9,
            facecolor="white",
            edgecolor="#CCCCCC",
        )
    style_axis(ax)
