"""Violation rule sunburst: inner ring = 3 channels, outer ring = sub-categories."""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
import numpy as np
from matplotlib.colors import to_rgba

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_style import apply_paper_style

apply_paper_style()

OUT_DIR = Path(__file__).resolve().parent / "images"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── data ────────────────────────────────────────────────────────────────────
# 3 channels (inner ring), each with sub-categories (outer ring)
CHANNELS = [
    {
        "name": "Tool",
        "accent": "#E8975C",
        "subs": [
            ("Forbidden Ordinary Tools", 7.00),
            ("Required Ordinary Tools",  3.14),
        ],
    },
    {
        "name": "Resource",
        "accent": "#6AAAD4",
        "subs": [
            ("Forbidden Resource Tools", 20.90),
            ("Required Resource Tools",   5.38),
        ],
    },
    {
        "name": "Information\nFlow",
        "accent": "#8BBD8B",
        "subs": [
            ("Communication Rules", 13.00),
            ("Data-leak Rules",     10.82),
        ],
    },
]


def _shades(base_hex: str, n: int) -> list[str]:
    """Light-to-mid shades from a base colour."""
    rgba = to_rgba(base_hex)
    out = []
    for i in range(n):
        t = 0.35 + 0.45 * i / max(n - 1, 1)
        r = rgba[0] * t + 1.0 * (1 - t)
        g = rgba[1] * t + 1.0 * (1 - t)
        b = rgba[2] * t + 1.0 * (1 - t)
        out.append(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")
    return out


# flatten for pie
inner_vals, inner_colors, inner_labels = [], [], []
outer_vals, outer_colors, outer_labels = [], [], []

for ch in CHANNELS:
    total = sum(v for _, v in ch["subs"])
    inner_vals.append(total)
    inner_colors.append(ch["accent"])
    inner_labels.append(ch["name"])

    shades = _shades(ch["accent"], len(ch["subs"]))
    for (label, val), shade in zip(ch["subs"], shades):
        outer_vals.append(val)
        outer_colors.append(shade)
        outer_labels.append(label)

GRAND = sum(outer_vals)

# ── draw ────────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(9, 9), facecolor="white")
ax.set_aspect("equal")

R_OUTER = 1.0
W_OUTER = 0.35
R_INNER = R_OUTER - W_OUTER
W_INNER = 0.25

# outer ring — sub-categories
wedges_outer, _ = ax.pie(
    outer_vals, colors=outer_colors, startangle=90, counterclock=False,
    radius=R_OUTER,
    wedgeprops=dict(width=W_OUTER, edgecolor="white", linewidth=2.0),
)

# inner ring — channels
wedges_inner, _ = ax.pie(
    inner_vals, colors=inner_colors, startangle=90, counterclock=False,
    radius=R_INNER,
    wedgeprops=dict(width=W_INNER, edgecolor="white", linewidth=2.5),
)

# ── inner ring labels (channel name) — large, centered in arc ───────────────
for wedge, name, total in zip(wedges_inner, inner_labels, inner_vals):
    ang = (wedge.theta1 + wedge.theta2) / 2
    mid_r = (R_INNER - W_INNER) + W_INNER / 2
    x = mid_r * np.cos(np.radians(ang))
    y = mid_r * np.sin(np.radians(ang))
    rot = ang - 90
    if rot > 90:
        rot -= 180
    if rot < -90:
        rot += 180
    ax.text(x, y, name, ha="center", va="center",
            fontsize=13, fontweight="bold", color="white", rotation=rot,
            linespacing=1.2,
            path_effects=[pe.withStroke(linewidth=3.5, foreground=to_rgba("black", 0.35))])

# ── outer ring labels — text INSIDE segments, rotated radially ──────────────
for wedge, label, val in zip(wedges_outer, outer_labels, outer_vals):
    ang = (wedge.theta1 + wedge.theta2) / 2
    mid_r = R_OUTER - W_OUTER / 2

    x_txt = mid_r * np.cos(np.radians(ang))
    y_txt = mid_r * np.sin(np.radians(ang))

    # text reads outward: rotate along radius
    rot = ang
    if 90 < ang <= 270:
        rot = ang + 180
    # normalize
    while rot > 180:
        rot -= 360
    while rot < -180:
        rot += 360

    display = f"{label}\n{val:.2f}"
    ax.text(x_txt, y_txt, display,
            ha="center", va="center", fontsize=8.0, color="#333333",
            fontweight="bold", rotation=rot, rotation_mode="anchor",
            linespacing=1.1,
            path_effects=[pe.withStroke(linewidth=2.5, foreground="white")])

# ── center hub ──────────────────────────────────────────────────────────────
hub_r = R_INNER - W_INNER - 0.01
circle = plt.Circle((0, 0), hub_r, color="white", ec="#D0D5DD", lw=1.8, zorder=5)
ax.add_patch(circle)
ax.text(0, 0.03, "Violation\nRules", ha="center", va="center",
        fontsize=14, fontweight="bold", color="#2C3E50",
        linespacing=1.3, zorder=6)
ax.text(0, -0.10, f"{GRAND:.2f} avg / task", ha="center", va="center",
        fontsize=9.5, color="#777777", zorder=6)

ax.set_xlim(-1.15, 1.15)
ax.set_ylim(-1.15, 1.15)
ax.axis("off")

fig.tight_layout(pad=0.5)

# ── save all versions ───────────────────────────────────────────────────────
OUT_PNG = OUT_DIR / "violation_sunburst.png"
OUT_PDF_TEXT = OUT_DIR / "violation_sunburst_with_text.pdf"
OUT_PDF_BLANK = OUT_DIR / "violation_sunburst_blank.pdf"

fig.savefig(OUT_PNG, dpi=300, bbox_inches="tight", facecolor="white")
fig.savefig(OUT_PDF_TEXT, bbox_inches="tight", facecolor="white")
print(f"Saved: {OUT_PNG}")
print(f"Saved: {OUT_PDF_TEXT}")

from matplotlib.text import Text
for t in fig.findobj(match=Text):
    t.set_visible(False)
fig.savefig(OUT_PDF_BLANK, bbox_inches="tight", facecolor="white")
plt.close(fig)
print(f"Saved: {OUT_PDF_BLANK}")
