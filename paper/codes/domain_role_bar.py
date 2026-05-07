"""Simple bar chart: unique role count per domain, full frame border."""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_style import apply_paper_style, style_axes, FACE

apply_paper_style()

OUT = Path(__file__).resolve().parent / "images" / "domain_role_bar.png"
OUT.parent.mkdir(parents=True, exist_ok=True)

DOMAINS      = ["Daily Life", "E-commerce", "Finance", "Healthcare",
                "Legal", "Office", "Social", "Software Eng."]
UNIQUE_ROLES = [6, 14, 9, 9, 8, 7, 14, 5]

COLORS = ["#FDE8B0", "#FACDA0", "#A8D1E8", "#A8DFC8",
          "#F0ADBD", "#C8BDE0", "#A8E8C8", "#B8DCE8"]

fig, ax = plt.subplots(figsize=(5.5, 3.5), facecolor="white")

x = np.arange(len(DOMAINS))
bars = ax.bar(x, UNIQUE_ROLES, width=0.55, color=COLORS,
              edgecolor="none", linewidth=0, zorder=3)

for xi, val in zip(x, UNIQUE_ROLES):
    ax.text(xi, val + 0.3, str(val), ha="center", va="bottom",
            fontsize=9, fontweight="bold", color="#2C3E50")

style_axes(ax, grid_axis="y", full_frame=True, spine_color="#2C3E50",
           spine_linewidth=2.2)
ax.set_xticks(x)
ax.set_xticklabels(DOMAINS, fontsize=7.5, fontweight="bold", rotation=35, ha="right")
ax.set_ylabel("Number of Unique Roles", fontsize=9, fontweight="bold")
ax.set_ylim(0, 18)
ax.yaxis.set_major_locator(plt.MultipleLocator(2))
ax.set_title("Unique Agent Roles per Domain", fontsize=13,
             fontweight="bold", pad=10, color="#2C3E50")

fig.tight_layout(pad=1.2)

# ── save with-text PDF ──────────────────────────────────────────────────────
OUT_PDF_TEXT = OUT.with_name("domain_role_bar_with_text.pdf")
fig.savefig(OUT_PDF_TEXT, bbox_inches="tight", facecolor="white")
fig.savefig(OUT, dpi=300, bbox_inches="tight", facecolor="white")
print(f"Saved: {OUT}")
print(f"Saved: {OUT_PDF_TEXT}")

# ── save blank (no text) PDF ────────────────────────────────────────────────
from matplotlib.text import Text
for t in fig.findobj(match=Text):
    t.set_visible(False)

OUT_PDF_BLANK = OUT.with_name("domain_role_bar_blank.pdf")
fig.savefig(OUT_PDF_BLANK, bbox_inches="tight", facecolor="white")
plt.close(fig)
print(f"Saved: {OUT_PDF_BLANK}")
