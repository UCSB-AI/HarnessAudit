"""Domain Role Distribution — single nested donut.

Outer ring: individual roles (coloured by domain hue family).
Inner ring: domain sectors (solid domain colour).
"""
from __future__ import annotations

import sys
from pathlib import Path
from collections import OrderedDict

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
import numpy as np
from matplotlib.colors import to_rgba

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_style import apply_paper_style, FACE

apply_paper_style()

OUT = Path(__file__).resolve().parent / "images" / "domain_role_donuts.png"
OUT.parent.mkdir(parents=True, exist_ok=True)

# ── data ────────────────────────────────────────────────────────────────────
DOMAIN_ROLES = OrderedDict([
    ("Daily Life", OrderedDict([
        ("booking spec.", 21), ("payments & coverage", 21),
        ("concierge coord.", 19), ("vendor liaison", 19),
        ("insurance analyst", 4), ("concierge lead", 2),
    ])),
    ("E-commerce", OrderedDict([
        ("commerce ops mgr", 38), ("identity verifier", 29),
        ("case analyst", 26), ("order specialist", 25),
        ("knowledge spec.", 19), ("refund specialist", 15),
        ("other ×8", 24),
    ])),
    ("Finance", OrderedDict([
        ("relationship mgr", 40), ("compliance analyst", 33),
        ("operations analyst", 26), ("risk officer", 24),
        ("trader", 19), ("insurance analyst", 11),
        ("other ×3", 17),
    ])),
    ("Healthcare", OrderedDict([
        ("care coordinator", 30), ("clinical decision", 30),
        ("diagnostic intf.", 30), ("specialist consult.", 28),
        ("patient interviewer", 21), ("med recon spec.", 12),
        ("other ×3", 3),
    ])),
    ("Legal", OrderedDict([
        ("legal analyst", 20), ("filings spec.", 20),
        ("fact investigator", 19), ("exposure analyst", 15),
        ("legal ops mgr", 14), ("compliance analyst", 9),
        ("other ×2", 8),
    ])),
    ("Office", OrderedDict([
        ("operations mgr", 27), ("policy analyst", 27),
        ("incident ops", 20), ("vendor coord.", 15),
        ("finance ops", 13), ("workplace admin", 12),
        ("procurement", 7),
    ])),
    ("Social", OrderedDict([
        ("engage coord.", 18), ("fact checker", 13),
        ("reporter", 12), ("standards editor", 12),
        ("trust & safety", 12), ("community ops", 12),
        ("stakeholder coord.", 12), ("other ×7", 28),
    ])),
    ("Software Eng.", OrderedDict([
        ("backend dev", 10), ("qa engineer", 10),
        ("security reviewer", 5), ("devops", 5),
        ("tech lead", 5),
    ])),
])

# domain accent colour (used for inner ring + legend)
DOMAIN_ACCENT = OrderedDict([
    ("Daily Life",     "#F7C548"),
    ("E-commerce",     "#E8752C"),
    ("Finance",        "#2274A5"),
    ("Healthcare",     "#2E9070"),
    ("Legal",          "#C73659"),
    ("Office",         "#7A6BB8"),
    ("Social",         "#1AA56A"),
    ("Software Eng.",  "#4A9BC8"),
])


def _shade_family(base_hex: str, n: int) -> list[str]:
    """Generate *n* shades from a base colour (lighter → darker)."""
    rgba = to_rgba(base_hex)
    shades = []
    for i in range(n):
        t = 0.30 + 0.55 * i / max(n - 1, 1)   # mix ratio with white
        r = rgba[0] * t + 1.0 * (1 - t)
        g = rgba[1] * t + 1.0 * (1 - t)
        b = rgba[2] * t + 1.0 * (1 - t)
        shades.append(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")
    return shades


# ── build flat lists for the two rings ──────────────────────────────────────
outer_vals, outer_colors, outer_labels = [], [], []
inner_vals, inner_colors, inner_labels = [], [], []

for domain, roles in DOMAIN_ROLES.items():
    accent = DOMAIN_ACCENT[domain]
    n_roles = len(roles)
    shades = _shade_family(accent, n_roles)
    domain_total = sum(roles.values())

    inner_vals.append(domain_total)
    inner_colors.append(accent)
    inner_labels.append(domain)

    for (role, count), shade in zip(roles.items(), shades):
        outer_vals.append(count)
        outer_colors.append(shade)
        outer_labels.append(role)

GRAND_TOTAL = sum(outer_vals)

# ── draw ────────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(13, 13), facecolor="white")
ax.set_aspect("equal")

R_OUTER = 1.0
W_OUTER = 0.28
R_INNER = R_OUTER - W_OUTER
W_INNER = 0.22

# outer ring — roles
wedges_outer, _ = ax.pie(
    outer_vals, colors=outer_colors, startangle=90, counterclock=False,
    radius=R_OUTER,
    wedgeprops=dict(width=W_OUTER, edgecolor="white", linewidth=1.0),
)

# inner ring — domains
wedges_inner, _ = ax.pie(
    inner_vals, colors=inner_colors, startangle=90, counterclock=False,
    radius=R_INNER,
    wedgeprops=dict(width=W_INNER, edgecolor="white", linewidth=1.8),
)

# ── domain labels on inner ring ─────────────────────────────────────────────
for wedge, domain, total in zip(wedges_inner, inner_labels, inner_vals):
    ang = (wedge.theta1 + wedge.theta2) / 2
    mid_r = (R_INNER - W_INNER) + W_INNER / 2
    x = mid_r * np.cos(np.radians(ang))
    y = mid_r * np.sin(np.radians(ang))

    # rotate text along the arc
    rot = ang - 90 if -90 <= (ang - 90) <= 90 else ang + 90
    if rot > 90:
        rot -= 180
    if rot < -90:
        rot += 180

    ax.text(x, y, f"{domain}\n{total}", ha="center", va="center",
            fontsize=7.0, fontweight="bold", color="white", rotation=rot,
            linespacing=1.1,
            path_effects=[pe.withStroke(linewidth=2.5, foreground=to_rgba("black", 0.35))])

# ── role labels on outer ring (only for slices >= 2.5% of total) ────────────
for wedge, label, val in zip(wedges_outer, outer_labels, outer_vals):
    pct = val / GRAND_TOTAL * 100
    if pct < 1.8:
        continue
    ang = (wedge.theta1 + wedge.theta2) / 2
    mid_r = (R_OUTER - W_OUTER) + W_OUTER / 2

    text_r = R_OUTER + 0.04
    x_txt = text_r * np.cos(np.radians(ang))
    y_txt = text_r * np.sin(np.radians(ang))

    far_r = R_OUTER + 0.38
    x_far = far_r * np.cos(np.radians(ang))
    y_far = far_r * np.sin(np.radians(ang))

    ha = "left" if np.cos(np.radians(ang)) >= 0 else "right"
    txt = f"{label} ({val})"

    ax.annotate(
        txt,
        xy=(x_txt, y_txt),
        xytext=(x_far, y_far),
        fontsize=6.5, color="#333333",
        ha=ha, va="center",
        arrowprops=dict(arrowstyle="-", color="#BBBBBB", lw=0.5),
        path_effects=[pe.withStroke(linewidth=2, foreground="white")],
    )

# ── center hub ──────────────────────────────────────────────────────────────
hub_r = R_INNER - W_INNER - 0.01
circle = plt.Circle((0, 0), hub_r, color="white", ec="#D0D5DD", lw=1.5, zorder=5)
ax.add_patch(circle)
ax.text(0, 0.025, "HarnessAudit-Bench", ha="center", va="center",
        fontsize=14, fontweight="bold", color="#2C3E50", zorder=6)
ax.text(0, -0.04, f"{GRAND_TOTAL} role-slots · 69 unique · 8 domains",
        ha="center", va="center", fontsize=8, color="#777777", zorder=6)

# ── legend (domain colours) ─────────────────────────────────────────────────
legend_patches = [
    mpatches.Patch(facecolor=DOMAIN_ACCENT[d], edgecolor="white", lw=0.5,
                   label=f"{d}  ({sum(DOMAIN_ROLES[d].values())})")
    for d in DOMAIN_ROLES
]
leg = ax.legend(
    handles=legend_patches, loc="lower center",
    bbox_to_anchor=(0.5, -0.08), ncol=4,
    frameon=True, framealpha=0.95, edgecolor="#D0D5DD",
    fontsize=9.5, handlelength=1.3, handletextpad=0.5, columnspacing=1.8,
)
leg.get_frame().set_linewidth(0.6)

ax.set_xlim(-1.65, 1.65)
ax.set_ylim(-1.65, 1.65)
ax.axis("off")

fig.suptitle("Agent Role Composition Across Domains",
             fontsize=16, fontweight="bold", color="#2C3E50", y=0.95)
fig.text(0.5, 0.92,
         "Inner ring: domains — Outer ring: individual agent roles (sized by task participation count)",
         ha="center", fontsize=10, color="#666666", style="italic")

fig.savefig(OUT, dpi=300, bbox_inches="tight", facecolor="white")
plt.close(fig)
print(f"Saved: {OUT}")
