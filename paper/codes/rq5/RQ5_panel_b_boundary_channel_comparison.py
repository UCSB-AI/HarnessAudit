import sys
from pathlib import Path

import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_style import save_figure
from rq1_common import IMAGES_DIR, load_base_runs
from harness_panels import compute_panel_b, draw_panel_b


OUTPUT_PATH = IMAGES_DIR / "RQ5_panel_b_boundary_channel_comparison.png"
LABELED_PATH = IMAGES_DIR / "RQ5_panel_b_boundary_channel_comparison_labeled.png"
OUTPUT_PDF_PATH = IMAGES_DIR / "RQ5_panel_b_boundary_channel_comparison.pdf"
LABELED_PDF_PATH = IMAGES_DIR / "RQ5_panel_b_boundary_channel_comparison_labeled.pdf"


def main() -> None:
    base_rows = load_base_runs(sar_mode="penalty")
    panel_rows = compute_panel_b(base_rows)
    if not panel_rows:
        raise RuntimeError("No boundary-channel comparison rows found.")

    fig, ax = plt.subplots(figsize=(6.0, 5.2), dpi=260, constrained_layout=True)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    draw_panel_b(ax, panel_rows)

    save_figure(fig, LABELED_PATH, strip_text_for_export=False)
    save_figure(fig, LABELED_PDF_PATH, strip_text_for_export=False)
    save_figure(fig, OUTPUT_PATH)
    save_figure(fig, OUTPUT_PDF_PATH)
    plt.close(fig)
    print(LABELED_PATH)
    print(LABELED_PDF_PATH)
    print(OUTPUT_PATH)
    print(OUTPUT_PDF_PATH)


if __name__ == "__main__":
    main()
