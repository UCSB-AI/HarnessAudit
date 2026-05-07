import sys
from pathlib import Path

import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain_panels import plot_domain_sar
from paper_style import save_figure
from rq2_common import IMAGES_DIR, load_base_runs


OUTPUT_PATH = IMAGES_DIR / "RQ2_panel_a_domain_sar.png"
LABELED_PATH = IMAGES_DIR / "RQ2_panel_a_domain_sar_labeled.png"
OUTPUT_PDF_PATH = IMAGES_DIR / "RQ2_panel_a_domain_sar.pdf"
LABELED_PDF_PATH = IMAGES_DIR / "RQ2_panel_a_domain_sar_labeled.pdf"


def main() -> None:
    rows = load_base_runs()
    if not rows:
        raise RuntimeError("No base runs found.")

    fig, ax = plt.subplots(figsize=(8.6, 6.6), dpi=260, constrained_layout=True)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    plot_domain_sar(ax, rows)

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
