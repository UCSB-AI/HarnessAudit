import sys
from pathlib import Path

import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_style import save_figure
from rq1_common import IMAGES_DIR
from domain_panels import collect_domain_role_scores, draw_role_radial, select_roles


OUTPUT_PATH = IMAGES_DIR / "RQ2_panel_b_role_violation_hotspots.png"
LABELED_PATH = IMAGES_DIR / "RQ2_panel_b_role_violation_hotspots_labeled.png"
OUTPUT_PDF_PATH = IMAGES_DIR / "RQ2_panel_b_role_violation_hotspots.pdf"
LABELED_PDF_PATH = IMAGES_DIR / "RQ2_panel_b_role_violation_hotspots_labeled.pdf"


def main() -> None:
    role_rows = collect_domain_role_scores()
    selected = select_roles(role_rows)
    if not any(selected.values()):
        raise RuntimeError("No domain-role data available.")

    fig, ax = plt.subplots(
        figsize=(7.7, 7.7),
        dpi=260,
        subplot_kw={"projection": "polar"},
        constrained_layout=True,
    )
    fig.patch.set_facecolor("white")
    draw_role_radial(ax, selected)

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
