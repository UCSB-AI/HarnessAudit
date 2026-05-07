from __future__ import annotations

import json
import os
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.text import Text


EDGE = "#2C3E50"
AXIS = "#333333"
GRID = "#CCCCCC"
FACE = "#FAFAFA"
TEXT_REPORT_DIR = Path(__file__).resolve().parents[1] / "NeurlPS26_Agent_Safety" / "figure_text_reports"
BLANK_FIGURE_TEXT = os.environ.get("MASP_KEEP_FIGURE_TEXT", "").lower() not in {"1", "true", "yes"}

# Plotting scripts gate ax.legend(...) on this; keep True when we need in-figure legend mapping.
SHOW_FIGURE_LEGENDS = True

PALETTE_BINARY = {
    "primary": "#2274A5",
    "secondary": "#C73659",
    "edge": EDGE,
}

ADH_CMAP_V4 = LinearSegmentedColormap.from_list(
    "adh_vivid",
    [
        (0.00, "#C73659"),
        (0.25, "#F39237"),
        (0.50, "#F7C548"),
        (0.75, "#5DBE9A"),
        (1.00, "#2274A5"),
    ],
    N=256,
)

PAPER_PALETTE = {
    "cmap": ADH_CMAP_V4,
    "systems": {
        "OpenClaw": "#2274A5",
        "Claude Code": "#5DBE9A",
        "Codex": "#C73659",
    },
    "channels": {
        "tool": "#2274A5",
        "resource": "#C73659",
        "flow": "#F39237",
    },
    "marginal": "#2274A5",
    "baseline": "#7A7A7A",
}

PALETTE_CAT4 = ["#5DBE9A", "#2274A5", "#C73659", "#F39237"]
PALETTE_PASTEL = ["#5DBE9A", "#F7C548", "#C73659", "#F39237", "#7A6BB8", "#2274A5"]
PALETTE_HARNESS = {
    "openclaw": PAPER_PALETTE["systems"]["OpenClaw"],
    "claude": PAPER_PALETTE["systems"]["Claude Code"],
    "codex": PAPER_PALETTE["systems"]["Codex"],
    "oai": "#F7C548",
    "adk": "#7A6BB8",
}
PALETTE_MODELS = {
    "gpt-5.4": "#2274A5",
    "claude-opus-4-6": "#5BAA8E",
    "claude-sonnet-4-6": "#5DBE9A",
    "gemini-3.1-pro-preview": "#7A6BB8",
    "glm-5v-turbo": "#F39237",
    "kimi-k2.6": "#C73659",
    "qwen3.5-plus": "#66788A",
}
PALETTE_CHANNELS = {
    "ordinary_tool": PAPER_PALETTE["channels"]["tool"],
    "resource_tool": PAPER_PALETTE["channels"]["resource"],
    "communication": PAPER_PALETTE["channels"]["flow"],
    "data_leak": "#7A6BB8",
}


def apply_paper_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Times New Roman", "STIXGeneral", "DejaVu Serif"],
            "mathtext.fontset": "stix",
            "font.size": 10,
            "axes.labelsize": 11,
            "axes.titlesize": 12,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.fontsize": 9,
            "axes.linewidth": 0.8,
            "axes.edgecolor": AXIS,
            "axes.facecolor": FACE,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.facecolor": "#FFFFFF",
            "axes.grid": True,
            "axes.grid.axis": "y",
            "grid.color": GRID,
            "grid.linewidth": 0.5,
            "grid.linestyle": "--",
            "grid.alpha": 0.5,
            "figure.dpi": 150,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def style_axes(
    ax,
    *,
    grid_axis: str = "y",
    facecolor: str = FACE,
    full_frame: bool = False,
    spine_color: str | None = None,
    spine_linewidth: float | None = None,
) -> None:
    ax.set_facecolor(facecolor)
    ax.grid(False)
    if grid_axis:
        ax.grid(True, axis=grid_axis, linestyle="--", linewidth=0.5, color=GRID, alpha=0.5, zorder=0)
    edge = spine_color if spine_color is not None else AXIS
    if spine_linewidth is not None:
        lw = spine_linewidth
    else:
        lw = 2.2 if full_frame else 0.8
    if full_frame:
        for spine in ("top", "right", "left", "bottom"):
            ax.spines[spine].set_visible(True)
            ax.spines[spine].set_color(edge)
            ax.spines[spine].set_linewidth(lw)
    else:
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)
        for spine in ["left", "bottom"]:
            ax.spines[spine].set_color(edge)
            ax.spines[spine].set_linewidth(lw)
    ax.tick_params(axis="both", labelsize=9, length=3.5, width=0.8, color="#6B7280")


def add_panel_label(ax, label: str, title: str | None = None, *, y: float = -0.23) -> None:
    text = label if title is None else f"{label} {title}"
    ax.text(
        0.5,
        y,
        text,
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=10,
        style="italic",
        color="#333333",
        clip_on=False,
    )


def _classify_text(fig, text: Text) -> tuple[int | None, str]:
    for ax_idx, ax in enumerate(fig.axes):
        if text is ax.title:
            return ax_idx, "title"
        if text is ax.xaxis.label:
            return ax_idx, "xlabel"
        if text is ax.yaxis.label:
            return ax_idx, "ylabel"
        if text in ax.get_xticklabels():
            return ax_idx, "xtick"
        if text in ax.get_yticklabels():
            return ax_idx, "ytick"
        legend = ax.get_legend()
        if legend is not None:
            if text is legend.get_title():
                return ax_idx, "legend_title"
            if text in legend.get_texts():
                return ax_idx, "legend_label"
        if text.axes is ax:
            return ax_idx, "annotation"
    return None, "figure_text"


def _collect_text_entries(fig, output_path: Path) -> list[dict[str, object]]:
    try:
        fig.canvas.draw()
    except Exception:
        pass

    entries: list[dict[str, object]] = []
    seen: set[int] = set()
    for text in fig.findobj(match=Text):
        if id(text) in seen or not text.get_visible():
            continue
        seen.add(id(text))
        content = text.get_text()
        if not content or not content.strip():
            continue
        ax_idx, kind = _classify_text(fig, text)
        x, y = text.get_position()
        entries.append(
            {
                "kind": kind,
                "axes_index": ax_idx,
                "text": content,
                "position": [round(float(x), 4), round(float(y), 4)],
                "fontsize": round(float(text.get_fontsize()), 2),
            }
        )
    return entries


def _write_text_report(output_path: Path, entries: list[dict[str, object]]) -> None:
    TEXT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = TEXT_REPORT_DIR / f"{output_path.stem}.json"
    unique_text = []
    seen_text = set()
    for entry in entries:
        text = str(entry["text"])
        if text not in seen_text:
            seen_text.add(text)
            unique_text.append(text)
    report_path.write_text(
        json.dumps(
            {
                "image": str(output_path),
                "text_count": len(entries),
                "unique_text": unique_text,
                "entries": entries,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def _blank_figure_text(fig) -> None:
    for text in fig.findobj(match=Text):
        text.set_alpha(0.0)
        text.set_color((0, 0, 0, 0))


def save_figure(
    fig,
    output_path: Path,
    *,
    strip_text_for_export: bool | None = None,
    bbox_extra_artists: list | None = None,
    pad_inches: float | None = None,
) -> None:
    """Save ``fig`` to ``output_path``.

    When ``strip_text_for_export`` is ``None``, use :data:`BLANK_FIGURE_TEXT`
    (strip axis/title text for camera-ready export and write a JSON sidecar).
    Pass ``False`` to keep tick labels, axis labels, and titles in the PNG.

    ``bbox_extra_artists`` is passed to ``savefig`` so ``bbox_inches='tight'``
    can include figure-level frames (e.g. a ``Rectangle`` in ``transFigure``).

    ``pad_inches`` overrides the default tight padding around the saved bbox.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if strip_text_for_export is None:
        strip = BLANK_FIGURE_TEXT
    else:
        strip = strip_text_for_export
    if strip:
        entries = _collect_text_entries(fig, output_path)
        _write_text_report(output_path, entries)
        _blank_figure_text(fig)
    save_kw: dict[str, object] = {
        "bbox_inches": "tight",
        "pad_inches": 0.05 if pad_inches is None else pad_inches,
        "facecolor": "white",
    }
    if bbox_extra_artists:
        save_kw["bbox_extra_artists"] = bbox_extra_artists
    fig.savefig(output_path, **save_kw)
