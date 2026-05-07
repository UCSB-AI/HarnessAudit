import sys
from pathlib import Path

import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from panels import plot_panel_b
from paper_style import save_figure
from rq1_common import IMAGES_DIR, load_base_runs


OUTPUT_PATH = IMAGES_DIR / "RQ1_panel_b_actions_violations.png"
LABELED_PATH = IMAGES_DIR / "RQ1_panel_b_actions_violations_labeled.png"
OUTPUT_PDF_PATH = IMAGES_DIR / "RQ1_panel_b_actions_violations.pdf"
LABELED_PDF_PATH = IMAGES_DIR / "RQ1_panel_b_actions_violations_labeled.pdf"


def main() -> None:
    rows = load_base_runs()
    if not rows:
        raise RuntimeError("No base runs found.")

    fig, ax = plt.subplots(figsize=(8.2, 5.6), dpi=260, constrained_layout=True)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    plot_panel_b(ax, rows)

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
