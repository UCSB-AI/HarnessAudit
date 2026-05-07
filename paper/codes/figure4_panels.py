"""Figure 4 panels: Agent Role Distribution & Violation Source Breakdown."""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_style import apply_paper_style, style_axes, FACE, AXIS, GRID

# ── output ──────────────────────────────────────────────────────────────────
OUT_DIR = Path(__file__).resolve().parent / "images"
OUT_DIR.mkdir(parents=True, exist_ok=True)

apply_paper_style()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PANEL A — Agent Role Distribution by Domain
#  Each domain has its OWN set of unique roles (different names, different n)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# domain display order (matches original figure left→right)
# source: data.md per-domain role lists + per-domain stats table
# bar total  = unique_roles (shown as number on top of bar)
# each segment height = 1 (one unique role = one unit)
# we use avg_roles_per_task as annotation only

DOMAIN_META = [
    # (display_name, unique_roles, avg_roles_per_task, [role_labels...])
    ("Finance", 9, 4.25, [
        "collateral_analyst", "compliance_analyst", "credit_policy_specialist",
        "insurance_coverage_analyst", "operations_analyst", "relationship_manager",
        "research_analyst", "risk_officer", "trader",
    ]),
    ("E-commerce", 14, 4.63, [
        "case_analyst", "case_router", "commerce_ops_manager", "crm_sales_analyst",
        "identity_verifier", "knowledge_compliance_specialist", "knowledge_specialist",
        "order_resolution_specialist", "order_specialist", "payment_specialist",
        "payments_refunds_specialist", "refund_specialist",
        "revenue_approval_specialist", "revenue_specialist",
    ]),
    ("Healthcare", 9, 5.13, [
        "cardiology_consultant", "care_coordinator", "care_plan_reviewer",
        "clinical_decision_specialist", "diagnostic_interface",
        "imaging_review_specialist", "medication_reconciliation_specialist",
        "patient_interviewer", "specialist_consultant",
    ]),
    ("Office", 7, 4.48, [
        "finance_ops_analyst", "incident_operations_analyst", "operations_manager",
        "policy_analyst", "procurement_analyst", "vendor_coordinator",
        "workplace_services_admin",
    ]),
    ("Daily Life", 6, 4.10, [
        "booking_specialist", "concierge_coordinator", "concierge_lead",
        "insurance_coverage_analyst", "payments_and_coverage_specialist", "vendor_liaison",
    ]),
    ("Legal", 8, 5.25, [
        "compliance_analyst", "exposure_analyst", "fact_investigator",
        "filings_specialist", "legal_analyst", "legal_matter_lead",
        "legal_ops_manager", "standards_editor",
    ]),
    ("Social", 14, 4.96, [
        "ad_policy_analyst", "assignment_editor", "audience_research_editor",
        "community_operations_specialist", "engagement_coordinator", "fact_checker",
        "public_engagement_lead", "publication_editor", "reporter",
        "research_verification_specialist", "stakeholder_response_coordinator",
        "standards_editor", "trust_safety_reviewer", "visual_forensics_reporter",
    ]),
    ("Software", 5, 3.50, [
        "backend_dev", "devops", "qa_engineer", "security_reviewer", "tech_lead",
    ]),
]

# --- color palette: cycle through 14 distinct colors for roles within each domain
# roles within one domain get sequential shades; domains use different hue families
DOMAIN_HUE_FAMILIES = [
    # Finance – blue family
    ["#174D73", "#1F6A9E", "#2274A5", "#4A9BC8", "#7AB8D9", "#9FCCE6", "#BDE0F2", "#D4EDF9", "#EAF6FF"],
    # E-commerce – orange/amber family (14 roles)
    ["#7A3800", "#A54E00", "#D46A00", "#E8752C", "#F39237", "#F7B066", "#F9C78A", "#FBDAAE",
     "#FDE8CC", "#FEF1E5", "#FFF6EF", "#FFF9F5", "#FFFCFA", "#FFFFFF"],
    # Healthcare – teal family
    ["#0D4D3A", "#1A6E52", "#2E9070", "#3DAA87", "#5DBE9A", "#84CEAE", "#A8DEC4", "#C8EDD9", "#E4F6ED"],
    # Office – purple family
    ["#3A1F6B", "#4E2E8A", "#6040A8", "#7A6BB8", "#9489C8", "#AFA8D9", "#C8C3E8", "#DDD9F2"],
    # Daily Life – gold family
    ["#6B4800", "#9E6D00", "#C99000", "#E0A800", "#F7C548", "#FAD978", "#FCE89E", "#FEF3C8"],
    # Legal – crimson/rose family
    ["#6B0A1A", "#9E1428", "#C73659", "#D95C7A", "#E6809A", "#EFA4B8", "#F6C8D5", "#FAE0E8"],
    # Social – indigo/violet family (14 roles)
    ["#1E0A47", "#2D1469", "#3D1E8C", "#4E2D9E", "#6040A8", "#7A6BB8", "#9489C8", "#AFA8D9",
     "#C8C3E8", "#DDD9F2", "#EEEAF9", "#F5F2FC", "#FAF8FE", "#FDFCFF"],
    # Software – green family
    ["#1A4A1A", "#2E6E2E", "#3D8F3D", "#5BAA5B", "#80C080"],
]

fig_a, ax_a = plt.subplots(figsize=(9.0, 5.2))
ax_a.set_facecolor(FACE)

x_positions = np.arange(len(DOMAIN_META))
bar_w = 0.58

for xi, (meta, hues) in enumerate(zip(DOMAIN_META, DOMAIN_HUE_FAMILIES)):
    domain_name, n_roles, avg_per_task, roles = meta
    # each role = 1 unit height, stacked
    bottom = 0
    for ri, (role, color) in enumerate(zip(roles, hues)):
        ax_a.bar(xi, 1, bar_w, bottom=bottom, color=color,
                 edgecolor="white", linewidth=0.5, zorder=3)
        # label inside segment if tall enough (always 1 unit; annotate the middle)
        # shorten long role names
        short = role.replace("_", " ").replace("specialist", "spec.")
        short = short.replace("analyst", "analyst").replace("coordinator", "coord.")
        short = short.replace("consultant", "consult.").replace("reconciliation", "recon.")
        ax_a.text(xi, bottom + 0.5, short,
                  ha="center", va="center", fontsize=5.2,
                  color="white", fontweight="bold", zorder=4, clip_on=True)
        bottom += 1

    # total on top
    ax_a.text(xi, n_roles + 0.15, str(n_roles),
              ha="center", va="bottom", fontsize=9, fontweight="bold", color=AXIS, zorder=5)
    # avg annotation below bar
    ax_a.text(xi, -0.6, f"avg {avg_per_task}/task",
              ha="center", va="top", fontsize=7, color="#666666", zorder=5)

style_axes(ax_a, grid_axis="y")
ax_a.set_xticks(x_positions)
ax_a.set_xticklabels([m[0] for m in DOMAIN_META], fontsize=9.5, fontweight="bold")
ax_a.set_ylabel("Number of Unique Roles", fontsize=11)
ax_a.set_ylim(-1.2, 17)
ax_a.yaxis.set_major_locator(plt.MultipleLocator(2))
ax_a.set_title("Agent Role Distribution by Domain", fontsize=13, fontweight="bold", pad=10)
ax_a.text(0.5, 1.015,
          "Unique agent roles per domain — each segment represents one distinct role",
          transform=ax_a.transAxes, ha="center", va="bottom",
          fontsize=8.5, color="#555555", style="italic")

fig_a.tight_layout(pad=1.2)
fig_a.savefig(OUT_DIR / "figure4_panel_a_role_distribution.png", dpi=300, bbox_inches="tight",
              facecolor="white")
print("Saved panel A")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PANEL B — Violation Breakdown by Layer (L1 / L2 / L3)
#  L1: operation.tool_call  (ordinary tool boundaries)
#  L2: operation.resource   (resource-scope rules)
#  L3: information.communication + information.data_leak
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── Layer definitions & sub-category data ────────────────────────────────────
LAYERS = [
    {
        "label":       "L1\nOrdinary Tool",
        "code":        "V-OT  operation.tool_call",
        "total":       2957,
        "subs": [
            ("Forbidden Tool Call",   1471),
            ("Unnecessary Tool Call", 1486),
        ],
        "colors":      ["#E8752C", "#F7B066"],   # orange: dark → light
        "total_color": "#C75A10",
    },
    {
        "label":       "L2\nResource Access",
        "code":        "V-OR  operation.resource",
        "total":       3094,
        "subs": [
            ("Target / Resource\nMismatch",          1511),
            ("Role / Action\nOverreach",             1055),
            ("Redundant /\nDuplicate",                452),
            ("Sequencing /\nAuth Failure",             76),
        ],
        "colors":      ["#174D73", "#2274A5", "#5BA8D0", "#A8D4EC"],  # blue: dark → light
        "total_color": "#0D3A5C",
    },
    {
        "label":       "L3\nCommunication",
        "code":        "V-IC / V-ID  information.*",
        "total":       85,
        "subs": [
            ("Over-Disclosure",          32),
            ("Prompt Injection\nProp.",  23),
            ("Unsafe Message\nPassing",  30),
        ],
        "colors":      ["#2E9070", "#5DBE9A", "#A8DEC4"],  # teal: dark → light
        "total_color": "#1A6650",
    },
]

fig_b, ax_b = plt.subplots(figsize=(8.0, 5.2))
ax_b.set_facecolor(FACE)

BAR_W = 0.50
x_pos = np.array([0, 1, 2])

legend_patches = []

for ci, (layer, cx) in enumerate(zip(LAYERS, x_pos)):
    bottom = 0
    for (sub_label, val), color in zip(layer["subs"], layer["colors"]):
        ax_b.bar(cx, val, BAR_W, bottom=bottom, color=color,
                 edgecolor="white", linewidth=0.8, zorder=3)

        # value number always shown inside segment
        mid = bottom + val / 2
        if val >= 60:
            ax_b.text(cx, mid, f"{val:,}", ha="center", va="center",
                      fontsize=9, color="white", fontweight="bold", zorder=5)
            ax_b.text(cx, mid - val * 0.18, sub_label,
                      ha="center", va="center", fontsize=6.8,
                      color="white", alpha=0.92, zorder=5)
        elif val >= 20:
            ax_b.text(cx, mid, f"{val}", ha="center", va="center",
                      fontsize=8.5, color="white", fontweight="bold", zorder=5)
        else:
            # tiny segment: put label + value outside to the right
            ax_b.text(cx + BAR_W / 2 + 0.04, mid, f"{val}  {sub_label}",
                      ha="left", va="center", fontsize=7, color="#444444", zorder=5)

        # legend entry
        clean_label = sub_label.replace("\n", " ")
        legend_patches.append(mpatches.Patch(facecolor=color, edgecolor="white",
                                             linewidth=0.5, label=clean_label))
        bottom += val

    # total on top of bar
    total = layer["total"]
    ax_b.text(cx, total + layer["total"] * 0.018,
              f"{total:,}", ha="center", va="bottom",
              fontsize=12, fontweight="bold", color=layer["total_color"], zorder=5)

    # violation code annotation below x-axis label
    ax_b.text(cx, -total * 0.06, layer["code"],
              ha="center", va="top", fontsize=6.5, color="#888888",
              style="italic", zorder=5)

# dividers between layers
for x_div in [0.5, 1.5]:
    ax_b.axvline(x_div, color=GRID, linewidth=0.8, linestyle="--", zorder=1)

style_axes(ax_b, grid_axis="y")
ax_b.set_xticks(x_pos)
ax_b.set_xticklabels([l["label"] for l in LAYERS], fontsize=11, fontweight="bold")
ax_b.set_ylabel("Number of Violation Rules", fontsize=11)
ax_b.set_ylim(-220, 3600)
ax_b.yaxis.set_major_locator(plt.MultipleLocator(500))

ax_b.set_title("Violation Rule Breakdown by Protocol Layer", fontsize=13,
               fontweight="bold", pad=10)
ax_b.text(0.5, 1.015,
          "L1: tool-call boundaries · L2: resource-scope rules · L3: communication & data-leak constraints",
          transform=ax_b.transAxes, ha="center", va="bottom",
          fontsize=8.5, color="#555555", style="italic")

# group legend by layer with separators
from matplotlib.lines import Line2D
leg_handles = []
layer_titles = ["L1 — Ordinary Tool (V-OT)", "L2 — Resource Access (V-OR)",
                "L3 — Communication / Data Leak (V-IC / V-ID)"]
idx = 0
for li, layer in enumerate(LAYERS):
    leg_handles.append(Line2D([0], [0], color="none", label=f"  {layer_titles[li]}"))
    for j in range(len(layer["subs"])):
        leg_handles.append(legend_patches[idx])
        idx += 1

leg = ax_b.legend(
    handles=leg_handles,
    loc="upper right",
    frameon=True, framealpha=0.93, edgecolor=GRID,
    fontsize=7.5, ncol=1,
    handlelength=1.3, handletextpad=0.5,
    borderpad=0.8,
)
# bold the group title entries
for text, handle in zip(leg.get_texts(), leg.legend_handles):
    if isinstance(handle, Line2D) and handle.get_color() == "none":
        text.set_fontweight("bold")
        text.set_fontsize(8)
        text.set_color("#333333")
leg.get_frame().set_linewidth(0.6)

fig_b.tight_layout(pad=1.2)
fig_b.savefig(OUT_DIR / "figure4_panel_b_violation_source.png", dpi=300, bbox_inches="tight",
              facecolor="white")
print("Saved panel B")

plt.show()
