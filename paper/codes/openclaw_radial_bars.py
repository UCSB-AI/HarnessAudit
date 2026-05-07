"""OpenClaw Layer Profile — T2AV-Compass style radial bar chart.

Each of the 5 metrics radiates outward from a central hub.
Along each radial arm, 7 model bars are stacked side-by-side (like a
horizontal grouped bar chart rotated to the arm's angle).
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_style import apply_paper_style, FACE, AXIS, GRID

apply_paper_style()

OUT = Path(__file__).resolve().parent / "images" / "openclaw_radial_bars.png"
OUT.parent.mkdir(parents=True, exist_ok=True)

# ── data ────────────────────────────────────────────────────────────────────
METRICS = ["L1 Avg.", "L2 Avg.", "L3 Avg.", r"S@$\tau$40", "Overall"]

MODELS = [
    ("Gemini 3.1 Pro",    "gemini"),
    ("Kimi K2.6",         "kimi"),
    ("ChatGPT-5.4",       "gpt"),
    ("GLM 5V Turbo",      "glm"),
    ("Qwen 3.5 Plus",     "qwen"),
    ("Claude Sonnet 4.6", "sonnet"),
    ("Claude Opus 4.6",   "opus"),
]

# rows: metrics × models  (ordered highest→lowest overall for visual impact)
DATA = {
    #                  L1    L2    L3   S@T40  Overall
    "gemini":        [0.66, 0.52, 0.34, 0.40, 0.32],
    "kimi":          [0.65, 0.46, 0.36, 0.29, 0.28],
    "gpt":           [0.52, 0.50, 0.33, 0.48, 0.25],
    "qwen":          [0.49, 0.50, 0.28, 0.38, 0.23],
    "glm":           [0.51, 0.47, 0.28, 0.40, 0.22],
    "sonnet":        [0.38, 0.53, 0.26, 0.39, 0.19],
    "opus":          [0.30, 0.60, 0.23, 0.41, 0.18],
}

MODEL_COLORS = {
    "gemini":  "#97c2dc",
    "kimi":    "#dfc56f",
    "gpt":     "#f2b9ad",
    "glm":     "#b99398",
    "qwen":    "#e67f7f",
    "sonnet":  "#84bea9",
    "opus":    "#b49cbc",
}

N_METRICS = len(METRICS)
N_MODELS  = len(MODELS)

# ── geometry ────────────────────────────────────────────────────────────────
base_angles = np.linspace(0, 2 * np.pi, N_METRICS, endpoint=False)
base_angles = base_angles + np.pi / 2  # start from top

R_INNER  = 0.65
R_MAX    = 3.5
VAL_MAX  = 0.70
BAR_ANGULAR_WIDTH = 0.065
BAR_GAP  = 0.012

def val_to_r(v: float) -> float:
    return R_INNER + (v / VAL_MAX) * (R_MAX - R_INNER)

# ── figure ──────────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(10, 10), facecolor="white")
ax = fig.add_subplot(111, polar=True)
ax.set_facecolor("white")
ax.set_theta_offset(np.pi / 2)
ax.set_theta_direction(-1)
ax.set_xticks([])
ax.set_yticks([])
ax.grid(False)
ax.spines["polar"].set_visible(False)

# ── concentric reference rings with value labels ────────────────────────────
theta_ring = np.linspace(0, 2 * np.pi, 300)
for ref_val in [0.2, 0.4, 0.6]:
    r = val_to_r(ref_val)
    ax.plot(theta_ring, np.full_like(theta_ring, r), color="#E8E8E8",
            linewidth=0.5, linestyle="--", zorder=0)
    ax.text(base_angles[0] + 0.35, r, f"{ref_val:.1f}",
            ha="center", va="center", fontsize=7, color="#AAAAAA", zorder=1)

# inner hub
ax.plot(theta_ring, np.full_like(theta_ring, R_INNER), color="#D0D5DD",
        linewidth=1.5, zorder=2)
ax.fill(theta_ring, np.full_like(theta_ring, R_INNER), color="white", zorder=2)

# ── draw bars per metric arm ────────────────────────────────────────────────
for mi, (metric, base_angle) in enumerate(zip(METRICS, base_angles)):
    # faint guide line
    ax.plot([base_angle, base_angle], [R_INNER * 0.95, R_MAX + 0.1],
            color="#DDD", linewidth=0.5, zorder=1)

    total_span = N_MODELS * BAR_ANGULAR_WIDTH + (N_MODELS - 1) * BAR_GAP
    start_angle = base_angle - total_span / 2

    for bi, (model_label, model_key) in enumerate(MODELS):
        val = DATA[model_key][mi]
        r_outer = val_to_r(val)
        theta_left = start_angle + bi * (BAR_ANGULAR_WIDTH + BAR_GAP)
        theta_right = theta_left + BAR_ANGULAR_WIDTH

        n_pts = 40
        theta_bar = np.linspace(theta_left, theta_right, n_pts)
        theta_patch = np.concatenate([theta_bar, theta_bar[::-1]])
        r_patch = np.concatenate([np.full(n_pts, R_INNER),
                                  np.full(n_pts, r_outer)[::-1]])
        ax.fill(theta_patch, r_patch, color=MODEL_COLORS[model_key],
                alpha=0.82, edgecolor="white", linewidth=0.4, zorder=3)

        # thin darker border on radial sides for crispness
        ax.plot([theta_left, theta_left], [R_INNER, r_outer],
                color=MODEL_COLORS[model_key], linewidth=0.3, alpha=0.5, zorder=4)
        ax.plot([theta_right, theta_right], [R_INNER, r_outer],
                color=MODEL_COLORS[model_key], linewidth=0.3, alpha=0.5, zorder=4)

        # value label at bar tip
        theta_mid = (theta_left + theta_right) / 2
        label_r = r_outer + 0.09
        angle_deg = np.degrees(theta_mid)
        rot = -angle_deg + 90
        if rot < -90:
            rot += 180
        elif rot > 90:
            rot -= 180
        ax.text(theta_mid, label_r, f"{val:.2f}",
                ha="center", va="center", fontsize=5.8,
                color=MODEL_COLORS[model_key],
                fontweight="bold", rotation=rot, rotation_mode="anchor",
                zorder=5)

    # ── metric label: placed horizontally using fig.text ────────────────────
    label_r_polar = R_MAX + 0.65
    # convert polar (theta, r) → display (x, y) in axes fraction
    # use ax.transData to get pixel coords then fig.transFigure.inverted()
    x_data = label_r_polar * np.cos(np.pi / 2 - base_angle)  # polar→cartesian
    y_data = label_r_polar * np.sin(np.pi / 2 - base_angle)
    # place in polar coords but with 0 rotation
    ax.text(base_angle, label_r_polar, metric,
            ha="center", va="center", fontsize=13,
            fontweight="bold", color="#2C3E50",
            rotation=0,
            zorder=6,
            bbox=dict(boxstyle="round,pad=0.2", facecolor="white",
                      edgecolor="none", alpha=0.8))

# ── central hub ─────────────────────────────────────────────────────────────
hub_circle = np.linspace(0, 2 * np.pi, 200)
ax.fill(hub_circle, np.full(200, R_INNER * 0.92), color="white", zorder=8)
ax.plot(hub_circle, np.full(200, R_INNER * 0.92), color="#B0B8C4",
        linewidth=1.2, zorder=8)
ax.text(0, 0, "OpenClaw\nLayer Profile", ha="center", va="center",
        fontsize=10, fontweight="bold", color="#2C3E50",
        linespacing=1.4, zorder=10)

# ── legend ──────────────────────────────────────────────────────────────────
legend_patches = [mpatches.Patch(facecolor=MODEL_COLORS[key], edgecolor="#E0E0E0",
                                 linewidth=0.5, label=label)
                  for label, key in MODELS]

leg = fig.legend(
    handles=legend_patches,
    loc="lower center",
    ncol=4,
    frameon=True, framealpha=0.95, edgecolor="#D0D5DD",
    fontsize=9.5, handlelength=1.4, handletextpad=0.5,
    columnspacing=1.5, borderpad=0.8,
    bbox_to_anchor=(0.5, 0.01),
)
leg.get_frame().set_linewidth(0.6)

ax.set_ylim(0, R_MAX + 1.0)

fig.suptitle("OpenClaw Main-Table Layer Profile", fontsize=15,
             fontweight="bold", y=0.97, color="#2C3E50")

fig.tight_layout(pad=1.0, rect=[0.0, 0.06, 1.0, 0.95])
fig.savefig(OUT, dpi=300, bbox_inches="tight", facecolor="white")
plt.close(fig)
print(f"Saved: {OUT}")
