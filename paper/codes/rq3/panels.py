import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle, Patch, Wedge

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_style import (
    EDGE,
    PALETTE_CHANNELS,
    SHOW_FIGURE_LEGENDS,
    apply_paper_style,
    style_axes,
)

from rq2_common import (
    domain_channel_sar,
    protocol_heatmap,
    violation_composition,
)


COMPOSITION_COLORS = {
    "ordinary_tool": PALETTE_CHANNELS["ordinary_tool"],
    "resource_tool": PALETTE_CHANNELS["resource_tool"],
    "communication": PALETTE_CHANNELS["communication"],
    "data_leak": PALETTE_CHANNELS["data_leak"],
}

apply_paper_style()


def style_bar_axis(ax) -> None:
    style_axes(ax, grid_axis="y")


def plot_panel_a(ax, rows) -> None:
    labels, data = protocol_heatmap(rows)
    if data.size == 0:
        raise RuntimeError("No data for protocol heatmap.")

    row_order = np.argsort(np.nanmean(data, axis=1))
    labels = [labels[idx] for idx in row_order]
    data = data[row_order, :]

    # Sequential white → navy (matplotlib Blues), similar to reference heatmaps.
    im = ax.imshow(data, cmap="Blues", vmin=0.0, vmax=1.0, aspect="auto")
    n_rows, n_cols = data.shape

    ax.set_xticks(np.arange(n_cols))
    ax.set_xticklabels(["Ordinary\nTools", "Resource\nAccesses", "Inter-agent\nFlow"], fontsize=9.2)
    ax.set_yticks(np.arange(n_rows))
    ax.set_yticklabels(labels, fontsize=8.8)

    ax.set_xticks(np.arange(-0.5, n_cols, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, n_rows, 1), minor=True)
    ax.grid(False)
    ax.grid(which="minor", color="#D6E4F3", linestyle="-", linewidth=1.15)
    ax.tick_params(which="minor", bottom=False, left=False)

    for i in range(n_rows):
        for j in range(n_cols):
            value = data[i, j]
            ax.text(
                j,
                i,
                f"{value:.2f}",
                ha="center",
                va="center",
                fontsize=8.0,
                color="#1F2937" if value < 0.56 else "white",
            )

    for spine in ax.spines.values():
        spine.set_visible(False)

    ax.set_title("Protocol Adherence by Channel", pad=8)
    cbar = ax.figure.colorbar(im, ax=ax, fraction=0.043, pad=0.035)
    cbar.ax.set_title("Adherence", fontsize=8.4, pad=6)
    cbar.outline.set_edgecolor(EDGE)
    cbar.outline.set_linewidth(0.8)
    cbar.ax.tick_params(labelsize=8.0, length=3)


def plot_panel_b(ax, rows, *, show_legend: bool | None = None) -> None:
    labels, values = violation_composition(rows)
    if values.size == 0:
        raise RuntimeError("No data for violation composition.")

    x = np.arange(values.shape[0], dtype=float)
    width = 0.39
    bottom = np.zeros(values.shape[0], dtype=float)
    components = [
        ("ordinary_tool", "Tool"),
        ("resource_tool", "Resource"),
        ("communication", "Communication"),
        ("data_leak", "Data leak"),
    ]
    for idx, (key, label) in enumerate(components):
        segment = values[:, idx]
        ax.bar(
            x,
            segment,
            width=width,
            bottom=bottom,
            color=COMPOSITION_COLORS[key],
            edgecolor=EDGE,
            linewidth=0.8,
            label=label,
            alpha=0.97,
            zorder=3,
        )
        bottom += segment

    ax.set_xlim(-0.6, len(x) - 0.4)
    ax.set_ylim(0, 1.0)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=7.8)
    ax.set_yticks(np.arange(0, 1.01, 0.2))
    ax.set_ylabel("Share of violations")
    ax.set_title("Violation Composition by System", pad=18)
    legend_on = SHOW_FIGURE_LEGENDS if show_legend is None else show_legend
    if legend_on:
        ax.legend(
            loc="upper center",
            bbox_to_anchor=(0.5, 1.02),
            ncol=2,
            frameon=True,
            framealpha=0.96,
            facecolor="white",
            edgecolor="#CCCCCC",
            fontsize=7.8,
            title="Violation type",
            title_fontsize=8.0,
            columnspacing=0.9,
            handlelength=1.3,
        )
    style_bar_axis(ax)


# RQ3 panel (b) labeled figure: fixed concentric donut rings (colored share vs gray remainder).
RQ3_VIOLATION_RING_SPECS = (
    # (fraction colored [0–1], label text); outer → middle → inner (share 从小到大 toward center).
    (72.4 / 100.0, "72.4%"),
    (63.04 / 100.0, "63.04%"),
    (52.23 / 100.0, "52.23%"),
)

RING_FILL_COLORS = (
    "#c4e4c4",
    "#91cbf9",
    "#ffcc80",
)
RING_GRAY = "#D8DDE4"
RING_GAP_DEG = 2.8
RING_EDGE_WHITE = "#FFFFFF"


def plot_violation_composition_concentric_rings(ax: plt.Axes, *, show_labels: bool = True) -> None:
    """Three concentric donut rings: colored arc = share; gray arc = remainder."""
    ax.set_aspect("equal")
    ax.axis("off")

    ring_geom = (
        (1.00, 0.78),
        (0.73, 0.51),
        (0.46, 0.24),
    )
    center_r = 0.18

    start_deg = 90.0
    usable = 360.0 - RING_GAP_DEG

    # Draw outer rings first; inner rings stack toward the center.
    for (r_out, r_in), (frac, pct_label), fill_color in zip(
        ring_geom, RQ3_VIOLATION_RING_SPECS, RING_FILL_COLORS
    ):
        w = r_out - r_in
        arc_deg = usable * float(np.clip(frac, 0.0, 1.0))
        gray_deg = usable - arc_deg

        t0 = start_deg
        t1 = t0 + arc_deg
        t2 = t1 + RING_GAP_DEG
        t3 = t2 + gray_deg

        ax.add_patch(
            Wedge(
                (0.0, 0.0),
                r_out,
                t0,
                t1,
                width=w,
                facecolor=fill_color,
                edgecolor=RING_EDGE_WHITE,
                linewidth=1.35,
                zorder=3,
            )
        )
        ax.add_patch(
            Wedge(
                (0.0, 0.0),
                r_out,
                t1,
                t2,
                width=w,
                facecolor=RING_EDGE_WHITE,
                edgecolor=RING_EDGE_WHITE,
                linewidth=0.8,
                zorder=4,
            )
        )
        ax.add_patch(
            Wedge(
                (0.0, 0.0),
                r_out,
                t2,
                t3,
                width=w,
                facecolor=RING_GRAY,
                edgecolor=RING_EDGE_WHITE,
                linewidth=1.35,
                zorder=3,
            )
        )

        if show_labels:
            mid_deg = t0 + arc_deg / 2.0
            mid_rad = np.deg2rad(mid_deg)
            r_mid = (r_out + r_in) / 2.0
            ax.text(
                r_mid * np.cos(mid_rad),
                r_mid * np.sin(mid_rad),
                pct_label,
                ha="center",
                va="center",
                fontsize=11.5 if r_out > 0.85 else 10.2 if r_out > 0.6 else 9.4,
                fontweight="bold",
                color="#1a1a1a",
                zorder=6,
            )

    ax.add_patch(Circle((0.0, 0.0), center_r, facecolor="white", edgecolor="none", zorder=5))

    handles = [
        Patch(
            facecolor=RING_FILL_COLORS[0],
            edgecolor="#BBBBBB",
            linewidth=0.65,
            label="Outer ring",
        ),
        Patch(
            facecolor=RING_FILL_COLORS[1],
            edgecolor="#BBBBBB",
            linewidth=0.65,
            label="Middle ring",
        ),
        Patch(
            facecolor=RING_FILL_COLORS[2],
            edgecolor="#BBBBBB",
            linewidth=0.65,
            label="Inner ring",
        ),
        Patch(
            facecolor=RING_GRAY,
            edgecolor="#BBBBBB",
            linewidth=0.65,
            label="Remainder",
        ),
    ]
    ax.legend(
        handles=handles,
        loc="upper left",
        bbox_to_anchor=(0.02, 0.98),
        bbox_transform=ax.transAxes,
        ncol=2,
        fontsize=8.4,
        frameon=True,
        framealpha=0.96,
        facecolor="white",
        edgecolor="#CCCCCC",
        title="Ring encoding",
        title_fontsize=8.6,
        columnspacing=0.95,
        handletextpad=0.55,
    )

    pad = 1.08
    outer_r = ring_geom[0][0]
    ax.set_xlim(-outer_r * pad, outer_r * pad)
    ax.set_ylim(-outer_r * pad, outer_r * pad)


def plot_panel_c(
    ax,
    rows,
    channel_colors=None,
    *,
    bar_width: float = 0.23,
    bar_edgecolor=EDGE,
    bar_linewidth=0.8,
    axes_full_frame: bool = False,
    axes_spine_color: str = "#000000",
    axes_spine_linewidth: float = 2.6,
) -> None:
    labels, data = domain_channel_sar(rows)
    if data.size == 0:
        raise RuntimeError("No data for domain channel SAR.")

    if channel_colors is None:
        c0 = COMPOSITION_COLORS["ordinary_tool"]
        c1 = COMPOSITION_COLORS["resource_tool"]
        c2 = COMPOSITION_COLORS["communication"]
    else:
        c0, c1, c2 = channel_colors

    x = np.arange(len(labels))
    width = bar_width
    ax.bar(
        x - width,
        data[:, 0],
        width,
        color=c0,
        edgecolor=bar_edgecolor,
        linewidth=bar_linewidth,
        label=r"$\mathrm{SAR}^{t}$",
        alpha=0.97,
        zorder=3,
    )
    ax.bar(
        x,
        data[:, 1],
        width,
        color=c1,
        edgecolor=bar_edgecolor,
        linewidth=bar_linewidth,
        label=r"$\mathrm{SAR}^{r}$",
        alpha=0.97,
        zorder=3,
    )
    ax.bar(
        x + width,
        data[:, 2],
        width,
        color=c2,
        edgecolor=bar_edgecolor,
        linewidth=bar_linewidth,
        label=r"$\mathrm{SAR}^{f}$",
        alpha=0.97,
        zorder=3,
    )

    ax.set_xlim(-0.6, len(x) - 0.4)
    ax.set_ylim(0, 1.0)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=24, ha="right", fontsize=8.0)
    ax.set_yticks(np.arange(0, 1.01, 0.25))
    ax.set_ylabel("Mean SAR score")
    ax.set_title("Domain-Level SAR by Channel", pad=8)
    ax.axhline(0.5, ls="--", color="gray", alpha=0.5, linewidth=0.9, zorder=2)
    if SHOW_FIGURE_LEGENDS:
        ax.legend(
            loc="upper center",
            bbox_to_anchor=(0.5, 1.03),
            ncol=3,
            frameon=True,
            framealpha=0.96,
            facecolor="white",
            edgecolor="#CCCCCC",
            fontsize=8.2,
        )
    if axes_full_frame:
        style_axes(
            ax,
            grid_axis="y",
            full_frame=True,
            spine_color=axes_spine_color,
            spine_linewidth=axes_spine_linewidth,
        )
    else:
        style_bar_axis(ax)


plot_protocol_heatmap = plot_panel_a
plot_violation_composition = plot_panel_b
plot_domain_channel_sar = plot_panel_c
