import sys
from pathlib import Path

import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from panels import plot_violation_composition, plot_violation_composition_concentric_rings
from paper_style import save_figure
from rq2_common import IMAGES_DIR, load_base_runs


OUTPUT_PATH = IMAGES_DIR / "RQ3_panel_b_violation_composition.png"
LABELED_PATH = IMAGES_DIR / "RQ3_panel_b_violation_composition_labeled.png"
OUTPUT_PDF_PATH = IMAGES_DIR / "RQ3_panel_b_violation_composition.pdf"
LABELED_PDF_PATH = IMAGES_DIR / "RQ3_panel_b_violation_composition_labeled.pdf"
RINGS_CLEAN_PDF_PATH = IMAGES_DIR / "RQ3_panel_b_violation_composition_rings_clean.pdf"


def main() -> None:
    fig_rings, ax_rings = plt.subplots(figsize=(7.2, 7.2), dpi=260)
    fig_rings.patch.set_facecolor("white")
    plot_violation_composition_concentric_rings(ax_rings)
    save_figure(fig_rings, LABELED_PATH, strip_text_for_export=False, pad_inches=0.08)
    save_figure(fig_rings, LABELED_PDF_PATH, strip_text_for_export=False, pad_inches=0.08)
    plt.close(fig_rings)

    fig_clean, ax_clean = plt.subplots(figsize=(7.2, 7.2), dpi=260)
    fig_clean.patch.set_facecolor("white")
    plot_violation_composition_concentric_rings(ax_clean, show_labels=False)
    save_figure(fig_clean, RINGS_CLEAN_PDF_PATH, strip_text_for_export=False, pad_inches=0.08)
    plt.close(fig_clean)

    rows = load_base_runs()
    if not rows:
        raise RuntimeError("No base runs found.")

    fig, ax = plt.subplots(figsize=(9.0, 6.0), dpi=260, constrained_layout=True)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    plot_violation_composition(ax, rows, show_legend=True)

    save_figure(fig, OUTPUT_PATH, strip_text_for_export=False)
    save_figure(fig, OUTPUT_PDF_PATH, strip_text_for_export=False)
    plt.close(fig)
    print(LABELED_PATH)
    print(LABELED_PDF_PATH)
    print(RINGS_CLEAN_PDF_PATH)
    print(OUTPUT_PATH)
    print(OUTPUT_PDF_PATH)


if __name__ == "__main__":
    main()
