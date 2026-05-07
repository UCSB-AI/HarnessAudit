from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_PNG = OUTPUT_DIR / "fig1_bar.png"
OUTPUT_PDF = OUTPUT_DIR / "fig1_bar.pdf"

LAYERS = ["Boundary Compliance", "Execution Fidelity", "System Stability"]
LAYER_COLORS = {
    "Boundary Compliance": "#2274A5",
    "Execution Fidelity": "#5DBE9A",
    "System Stability": "#7A6BB8",
}

DATA = [
    ("ChatGPT-5.4", [0.52, 0.50, 0.33]),
    ("Claude Opus\n4.6", [0.30, 0.60, 0.23]),
    ("Claude Sonnet\n4.6", [0.38, 0.53, 0.26]),
    ("Gemini 3.1\nPro", [0.66, 0.52, 0.34]),
    ("GLM 5V\nTurbo", [0.51, 0.47, 0.28]),
    ("Kimi K2.6", [0.65, 0.46, 0.36]),
    ("Qwen 3.5\nPlus", [0.49, 0.50, 0.28]),
]


def apply_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Times New Roman", "STIXGeneral", "DejaVu Serif"],
            "font.size": 10,
            "axes.labelsize": 11,
            "axes.titlesize": 12,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.fontsize": 9,
            "axes.linewidth": 0.8,
            "axes.edgecolor": "#333333",
            "axes.facecolor": "#FFFFFF",
            "figure.facecolor": "#FFFFFF",
            "axes.grid": True,
            "axes.grid.axis": "y",
            "grid.color": "#CCCCCC",
            "grid.linewidth": 0.5,
            "grid.linestyle": "--",
            "grid.alpha": 0.5,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def style_axes(ax: plt.Axes) -> None:
    ax.set_axisbelow(True)
    ax.grid(True, axis="y", linestyle="--", linewidth=0.5, color="#CCCCCC", alpha=0.5)
    ax.grid(False, axis="x")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#333333")
        ax.spines[spine].set_linewidth(0.8)
    ax.tick_params(axis="both", labelsize=9, length=3.5, width=0.8, color="#6B7280")


def draw_figure() -> plt.Figure:
    apply_style()
    models = [label for label, _ in DATA]
    values = np.array([scores for _, scores in DATA])
    x = np.arange(len(models))
    width = 0.24

    fig, ax = plt.subplots(figsize=(8.4, 4.6), dpi=260)
    style_axes(ax)

    for layer_idx, layer in enumerate(LAYERS):
        offset = (layer_idx - (len(LAYERS) - 1) / 2) * width
        bars = ax.bar(
            x + offset,
            values[:, layer_idx],
            width=width,
            color=LAYER_COLORS[layer],
            edgecolor="white",
            linewidth=0.8,
            label=layer,
            zorder=3,
        )
        for bar, score in zip(bars, values[:, layer_idx]):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                score + 0.015,
                f"{score:.2f}",
                ha="center",
                va="bottom",
                fontsize=7.8,
                fontweight="bold",
                color="#344054",
                clip_on=False,
            )

    ax.set_ylim(0, 1.0)
    ax.set_yticks(np.arange(0, 1.01, 0.2))
    ax.set_xticks(x)
    ax.set_xticklabels(models, fontsize=8.5)
    ax.set_ylabel("Score")
    ax.set_title("OpenClaw Layer Scores by Model", pad=12, fontsize=12.5, fontweight="bold")
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, 1.02),
        ncol=3,
        frameon=False,
        fontsize=8.8,
        handlelength=1.2,
        columnspacing=1.2,
    )
    fig.subplots_adjust(left=0.075, right=0.99, top=0.84, bottom=0.16)
    return fig


def main() -> None:
    fig = draw_figure()
    fig.savefig(OUTPUT_PNG, facecolor="white", bbox_inches="tight", pad_inches=0.05)
    fig.savefig(OUTPUT_PDF, facecolor="white", bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    print(OUTPUT_PNG)
    print(OUTPUT_PDF)


if __name__ == "__main__":
    main()
