from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import to_rgb
from matplotlib.patches import Patch

CODES_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

sys.path.insert(0, str(CODES_ROOT))
sys.path.insert(0, str(REPO_ROOT))

from paper_style import apply_paper_style


TRACES_ROOT = REPO_ROOT / "masp" / "traces"
RESULTS_ROOT = REPO_ROOT / "masp" / "results"

apply_paper_style()

import sar_final  # noqa: E402

from rq2_common import domain_channel_sar  # noqa: E402
from rq3.panels import plot_domain_channel_sar  # noqa: E402


DOMAIN_ORDER = [
    "daily_life",
    "ecommerce",
    "finance",
    "healthcare",
    "legal_compliance",
    "office",
    "social_interaction",
    "software_engineering",
]

DOMAIN_LABELS = {
    "daily_life": "Daily\nlife",
    "ecommerce": "E-commerce",
    "finance": "Finance",
    "healthcare": "Healthcare",
    "legal_compliance": "Legal\ncompliance",
    "office": "Office",
    "social_interaction": "Social\ninteraction",
    "software_engineering": "Software\nengineering",
}

DOMAIN_COLORS = {
    "daily_life": "#3B6FA8",
    "ecommerce": "#E08B4A",
    "finance": "#2F8F9D",
    "healthcare": "#7A6BB8",
    "legal_compliance": "#4F9D5A",
    "office": "#D65D6E",
    "social_interaction": "#C27A2C",
    "software_engineering": "#5BAA8E",
}

PEAK_COLORS = {
    "daily_life": "#2D5682",
    "ecommerce": "#B86B32",
    "finance": "#236B76",
    "healthcare": "#5F51A0",
    "legal_compliance": "#387941",
    "office": "#A84350",
    "social_interaction": "#915C21",
    "software_engineering": "#3F806C",
}

MIN_OCCURRENCES = 25
TOP_ROLES_PER_DOMAIN = 5
MAX_DISPLAY_VALUE = 0.50
RING_VALUES = [0.15, 0.25, 0.40]

# RQ2 panel (a): domain-level SAR bars — SAR^t, SAR^r, SAR^f (pastels).
RQ2_DOMAIN_SAR_CHANNEL_COLORS = ("#c2b8e4", "#f6bcc5", "#f7bc93")
# Same-hue outlines (no black bar edges), for 2D or 3D bars.
RQ2_DOMAIN_SAR_EDGE_COLORS = ("#5a8a52", "#b06d45", "#948c36")

SAR_CHANNEL_LEGEND_TEX = (
    r"$\mathrm{SAR}^{t}$",
    r"$\mathrm{SAR}^{r}$",
    r"$\mathrm{SAR}^{f}$",
)

ROLE_LABEL_OVERRIDES = {
    "payments_and_coverage_specialist": "Payments\nCoverage",
    "payments_refunds_specialist": "Payments\nRefunds",
    "research_analyst": "Research",
    "clinical_decision_specialist": "Clinical\nDecision",
    "legal_analyst": "Legal\nAnalysis",
    "workplace_services_admin": "Workplace\nServices",
    "publication_editor": "Publication\nEditor",
    "devops": "DevOps",
}


def blend_with_white(hex_color: str, amount: float = 0.86) -> tuple[float, float, float]:
    r, g, b = to_rgb(hex_color)
    return (
        r * (1 - amount) + amount,
        g * (1 - amount) + amount,
        b * (1 - amount) + amount,
    )


def result_path_for_trace(trace_path: Path) -> Path:
    return (RESULTS_ROOT / trace_path.relative_to(TRACES_ROOT)).with_suffix(".json")


def iter_base_traces():
    for trace_path in sorted(TRACES_ROOT.glob("*/*/*.jsonl")):
        rel = trace_path.relative_to(TRACES_ROOT)
        if rel.parts[0] == "perturbations":
            continue
        result_path = result_path_for_trace(trace_path)
        if result_path.is_file():
            yield trace_path


def empty_counts() -> dict[str, dict[str, int]]:
    return {
        channel: {"N": 0, "V_low": 0, "V_high": 0}
        for channel in sar_final.CHANNELS
    }


def role_label(role: str) -> str:
    if role in ROLE_LABEL_OVERRIDES:
        return ROLE_LABEL_OVERRIDES[role]
    label = role.replace("_", " ")
    for suffix in (
        " specialist",
        " analyst",
        " coordinator",
        " manager",
        " reviewer",
        " consultant",
        " officer",
        " engineer",
        " developer",
        " dev",
    ):
        if label.endswith(suffix):
            label = label[: -len(suffix)]
            break
    return label.title()


def parse_role_counts(trace_path: Path) -> dict[str, object]:
    actions: dict[int, dict] = {}
    decisions_by_seq: dict[int, list[dict]] = defaultdict(list)
    comms: list[dict] = []
    active_roles: set[str] = set()
    domain = ""
    task_id = ""
    trace_end_actions: int | None = None

    with trace_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            event_type = event.get("type", "")

            if event_type == "trace_start":
                domain = event.get("domain", "")
                task_id = event.get("task_id", "")
                continue

            if event_type == "tool_call":
                seq = event.get("sequence_num")
                role = event.get("agent_role", "")
                if seq is not None and role:
                    actions[seq] = {
                        "kind": "tool",
                        "role": role,
                        "tool": event.get("tool_name", ""),
                    }
                    active_roles.add(role)
                continue

            if event_type == "communication":
                seq = event.get("sequence_num")
                role = event.get("agent_role", "")
                if seq is not None and role:
                    comm = {
                        "kind": "comm",
                        "role": role,
                        "agent_role": role,
                        "target_role": event.get("target_role", ""),
                        "content": event.get("content", ""),
                    }
                    actions[seq] = comm
                    comms.append(comm)
                    active_roles.add(role)
                continue

            if event_type == "access_decision":
                if not event.get("violated"):
                    continue
                seq = event.get("action_sequence_num")
                code = event.get("violation_code", "")
                severity = event.get("severity", "low")
                if seq is not None and code:
                    decisions_by_seq[seq].append(
                        {"code": code, "severity": severity}
                    )
                continue

            if event_type == "trace_end":
                n_actions = event.get("n_actions")
                if isinstance(n_actions, int):
                    trace_end_actions = n_actions

    role_counts: dict[str, dict[str, dict[str, int]]] = defaultdict(empty_counts)
    resource_tools = sar_final._load_resource_tools(domain)

    for seq, action in actions.items():
        if action["kind"] != "tool":
            continue
        role = action["role"]
        channel = "rsrc" if action["tool"] in resource_tools else "tool"
        role_counts[role][channel]["N"] += 1
        severity = sar_final._max_matching_severity(
            decisions_by_seq.get(seq, []),
            sar_final.TOOL_CODES,
        )
        if severity == "low":
            role_counts[role][channel]["V_low"] += 1
        elif severity == "high":
            role_counts[role][channel]["V_high"] += 1

    if task_id:
        info_rules = [
            rule
            for rule in sar_final._load_info_rules(task_id)
            if sar_final._rule_is_cleaned_flow_rule(rule)
        ]
        for role in active_roles:
            role_comms = [comm for comm in comms if comm["agent_role"] == role]
            fireable_rules = [
                rule
                for rule in info_rules
                if rule.role == role and sar_final._rule_is_fireable(rule, role_comms)
            ]
            role_counts[role]["flow"]["N"] = len(fireable_rules)
            for comm in role_comms:
                severities = {
                    rule.severity
                    for rule in fireable_rules
                    if sar_final._communication_matches_info_rule(comm, rule, domain)
                }
                if not severities:
                    continue
                if "high" in severities:
                    role_counts[role]["flow"]["V_high"] += 1
                else:
                    role_counts[role]["flow"]["V_low"] += 1

    return {
        "domain": domain,
        "task_id": task_id,
        "n_actions": trace_end_actions if trace_end_actions is not None else len(actions),
        "role_counts": dict(role_counts),
    }


def role_sar_mean(counts: dict[str, dict[str, int]]) -> float:
    return float(
        np.mean(
            [
                sar_final._penalty_score(counts[channel])
                for channel in sar_final.CHANNELS
            ]
        )
    )


def collect_domain_role_scores() -> list[dict]:
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    supports: dict[tuple[str, str], int] = defaultdict(int)

    for trace_path in iter_base_traces():
        try:
            row = parse_role_counts(trace_path)
        except Exception:
            continue
        if int(row["n_actions"]) <= sar_final.DIRTY_ACTION_THRESHOLD:
            continue
        domain = row["domain"]
        if not domain:
            continue
        for role, counts in row["role_counts"].items():
            if not role:
                continue
            active_n = sum(counts[channel]["N"] for channel in sar_final.CHANNELS)
            if active_n == 0:
                continue
            values[(domain, role)].append(role_sar_mean(counts))
            supports[(domain, role)] += 1

    rows: list[dict] = []
    for (domain, role), scores in values.items():
        if len(scores) < MIN_OCCURRENCES:
            continue
        sar_mean = float(np.mean(scores))
        rows.append(
            {
                "domain": domain,
                "role": role,
                "role_label": role_label(role),
                "support": supports[(domain, role)],
                "sar_mean": sar_mean,
                "violation_rate": max(0.0, 1.0 - sar_mean),
            }
        )
    return rows


def select_roles(rows: list[dict]) -> dict[str, list[dict]]:
    selected: dict[str, list[dict]] = {}
    for domain in DOMAIN_ORDER:
        candidates = [row for row in rows if row["domain"] == domain]
        candidates.sort(
            key=lambda row: (
                row["violation_rate"],
                np.log1p(row["support"]) * 0.03,
            ),
            reverse=True,
        )
        selected[domain] = candidates[:TOP_ROLES_PER_DOMAIN]
    return selected


def add_rotated_label(ax, theta: float, radius: float, text: str, **kwargs) -> None:
    ax.text(
        theta,
        radius,
        text,
        rotation=0,
        rotation_mode="anchor",
        ha="center",
        **kwargs,
    )


def draw_role_radial(ax: plt.Axes, selected: dict[str, list[dict]]) -> None:
    inner_radius = 0.30
    outer_radius = 1.00
    radial_scale = outer_radius - inner_radius
    n_domains = len(DOMAIN_ORDER)
    sector_span = 2 * np.pi / n_domains
    sector_gap = 0.085
    threshold_value = 0.25
    threshold_radius = inner_radius + (threshold_value / MAX_DISPLAY_VALUE) * radial_scale

    ax.set_facecolor("white")
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_ylim(0, outer_radius + 0.24)
    ax.set_axis_off()

    theta_full = np.linspace(0, 2 * np.pi, 720)
    for value, color, width, alpha in [
        (0.15, "#9AA3AE", 0.95, 0.35),
        (0.25, "#D67878", 1.35, 0.90),
        (0.40, "#9AA3AE", 0.95, 0.45),
    ]:
        ax.plot(
            theta_full,
            np.full_like(theta_full, inner_radius + (value / MAX_DISPLAY_VALUE) * radial_scale),
            color=color,
            linewidth=width,
            linestyle=(0, (3, 3)),
            alpha=alpha,
            zorder=5,
        )

    for domain_idx, domain in enumerate(DOMAIN_ORDER):
        values = selected.get(domain, [])
        if not values:
            continue
        color = DOMAIN_COLORS[domain]
        peak_color = PEAK_COLORS[domain]
        sector_start = domain_idx * sector_span
        visible_start = sector_start + sector_gap / 2
        visible_end = sector_start + sector_span - sector_gap / 2
        visible_width = visible_end - visible_start
        sector_center = (visible_start + visible_end) / 2

        ax.bar(
            sector_center,
            radial_scale,
            width=visible_width,
            bottom=inner_radius,
            color=blend_with_white(color, amount=0.89),
            edgecolor="none",
            alpha=0.94,
            align="center",
            zorder=0,
        )

        slot_width = visible_width / len(values)
        bar_width = slot_width * 0.58
        bar_thetas = visible_start + slot_width * (np.arange(len(values)) + 0.5)
        violation_values = np.array([row["violation_rate"] for row in values], dtype=float)

        for bar_idx, (theta, risk, row) in enumerate(zip(bar_thetas, violation_values, values)):
            shade_amount = 0.13 + 0.18 * (bar_idx / max(1, len(values) - 1))
            bar_color = blend_with_white(color, amount=shade_amount)
            height = min(risk / MAX_DISPLAY_VALUE, 1.0) * radial_scale
            ax.bar(
                theta,
                height,
                width=bar_width,
                bottom=inner_radius,
                color=bar_color,
                edgecolor=color,
                linewidth=0.78,
                alpha=0.96,
                align="center",
                zorder=3,
            )
            if risk >= 0.40:
                ax.scatter(
                    [theta],
                    [inner_radius + height + 0.018],
                    s=22,
                    color=peak_color,
                    edgecolors="white",
                    linewidths=0.65,
                    zorder=6,
                )

        peak_idx = int(np.argmax(violation_values))
        peak_theta = bar_thetas[peak_idx]
        peak_radius = inner_radius + violation_values[peak_idx] * radial_scale + 0.045
        label_radius = (
            inner_radius
            + (max(float(violation_values[peak_idx]), threshold_value) / MAX_DISPLAY_VALUE)
            * radial_scale
            + 0.135
        )
        label_radius = max(label_radius, inner_radius + 0.60 * radial_scale)
        ax.scatter(
            [peak_theta],
            [peak_radius],
            s=54,
            color=peak_color,
            edgecolors=color,
            linewidths=1.2,
            zorder=7,
        )
        add_rotated_label(
            ax,
            peak_theta,
            min(outer_radius + 0.06, label_radius),
            values[peak_idx]["role_label"],
            va="center",
            fontsize=6.4,
            color="#22272E",
            zorder=8,
        )

        max_radius = inner_radius + violation_values.max() * radial_scale + 0.045
        arc_theta = np.linspace(visible_start + 0.015, visible_end - 0.015, 120)
        ax.plot(
            arc_theta,
            np.full_like(arc_theta, max_radius),
            color=color,
            linewidth=2.6,
            alpha=0.98,
            solid_capstyle="round",
            zorder=4,
        )

        ax.text(
            sector_center,
            outer_radius + 0.18,
            DOMAIN_LABELS[domain],
            ha="center",
            va="center",
            fontsize=9.8,
            weight="bold",
            color=color,
            rotation=0,
            rotation_mode="anchor",
            zorder=8,
        )

    ax.text(
        0,
        0,
        "Role\nViolation\nRate",
        ha="center",
        va="center",
        fontsize=12.0,
        weight="bold",
        color="#22272E",
    )
    ax.text(
        np.deg2rad(78),
        threshold_radius + 0.015,
        "0.25",
        fontsize=7.8,
        color="#B33A4A",
        ha="left",
        va="center",
    )


def _wireframe_box(
    ax,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    zmin: float,
    zmax: float,
    *,
    ec: str = "#8C8C8C",
    lw: float = 1.02,
) -> None:
    def edge(p: tuple[float, float, float], q: tuple[float, float, float]) -> None:
        ax.plot([p[0], q[0]], [p[1], q[1]], [p[2], q[2]], color=ec, lw=lw)

    bot = (
        (xmin, ymin, zmin),
        (xmax, ymin, zmin),
        (xmax, ymax, zmin),
        (xmin, ymax, zmin),
    )
    top = (
        (xmin, ymin, zmax),
        (xmax, ymin, zmax),
        (xmax, ymax, zmax),
        (xmin, ymax, zmax),
    )
    for i in range(4):
        edge(bot[i], bot[(i + 1) % 4])
        edge(top[i], top[(i + 1) % 4])
        edge(bot[i], top[i])


def _wall_grids(
    ax,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    zmin: float,
    zmax: float,
    *,
    zticks: np.ndarray,
) -> None:
    glc = "#8A8A8A"
    for z in zticks:
        if z <= zmin + 1e-6 or z >= zmax - 1e-6:
            continue
        ax.plot(
            [xmin, xmax],
            [ymax, ymax],
            [z, z],
            color=glc,
            lw=0.48,
            linestyle=(0, (3.2, 2.8)),
            alpha=0.72,
            zorder=0.5,
        )
        ax.plot(
            [xmin, xmin],
            [ymin, ymax],
            [z, z],
            color=glc,
            lw=0.48,
            linestyle=(0, (3.2, 2.8)),
            alpha=0.62,
            zorder=0.5,
        )


def plot_domain_sar_3d(ax, rows: list[dict]) -> None:
    """3D grouped bars: domains on X, SAR channels staggered in depth (Y), height = mean SAR."""
    labels, data = domain_channel_sar(rows)
    if data.size == 0:
        raise RuntimeError("No data for domain channel SAR.")

    n_d, n_c = data.shape
    if n_c != 3:
        raise ValueError(f"Expected 3 SAR channels, got {n_c}.")

    group_pitch = 1.22
    bar_dx = 0.24
    bar_dy = 0.30
    y_band = 0.50

    faces = list(RQ2_DOMAIN_SAR_CHANNEL_COLORS)
    edges = list(RQ2_DOMAIN_SAR_EDGE_COLORS)

    # Mimic the reference style: light 3D panes + visible dashed grids.
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.pane.fill = True
        axis.pane.set_facecolor((0.96, 0.96, 0.96, 0.75))
        axis.pane.set_edgecolor("#B0B0B0")
        axis._axinfo["grid"]["linewidth"] = 0.8
        axis._axinfo["grid"]["linestyle"] = "--"
        axis._axinfo["grid"]["color"] = (0.68, 0.68, 0.68, 0.6)
        axis.line.set_color("#9A9A9A")
        axis.line.set_linewidth(0.9)

    ax.grid(True)
    ax.view_init(elev=21, azim=-64)

    zmin, zmax = 0.0, 1.0
    zticks = np.arange(0.0, 1.001, 0.25)

    for i in range(n_d):
        for k in range(n_c):
            x0 = i * group_pitch + (k - (n_c - 1) / 2.0) * (bar_dx * 0.92)
            y0 = k * y_band
            h = float(data[i, k])
            ax.bar3d(
                x0,
                y0,
                0.0,
                bar_dx,
                bar_dy,
                h,
                color=faces[k],
                edgecolor=edges[k],
                linewidth=1.45,
                alpha=0.36,
                shade=False,
                zsort="average",
            )

    xmin = -group_pitch * 0.42
    xmax = (n_d - 1) * group_pitch + group_pitch * 0.52 + bar_dx
    ymin = -y_band * 0.35
    ymax = (n_c - 1) * y_band + bar_dy + y_band * 0.35

    _wall_grids(ax, xmin, xmax, ymin, ymax, zmin, zmax, zticks=zticks)

    xc_mid = np.linspace(0, (n_d - 1) * group_pitch, n_d)
    for xv in xc_mid:
        ax.plot(
            [xv, xv],
            [ymin, ymax],
            [zmin, zmin],
            color="#9C9C9C",
            lw=0.65,
            linestyle=(0, (4.5, 3.5)),
            alpha=0.8,
            zorder=0.4,
        )

    ax.plot(
        [xmin, xmax],
        [ymin, ymin],
        [0.5, 0.5],
        color="#7F7F7F",
        lw=1.05,
        linestyle=(0, (5.5, 3.5)),
        alpha=0.8,
        zorder=1,
    )

    _wireframe_box(ax, xmin, xmax, ymin, ymax, zmin, zmax)

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_zlim(zmin, zmax)

    tick_lbl = [s.replace("\n", " ") for s in labels]
    ax.set_xticks(xc_mid)
    ax.set_xticklabels(tick_lbl, rotation=8, ha="right", fontsize=8.4)
    ax.set_yticks([])
    ax.set_yticklabels([])
    ax.set_zticks(zticks)
    ax.set_zticklabels([f"{z:.2f}" for z in zticks], fontsize=8.2)
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_zlabel("Mean SAR score", labelpad=10, fontsize=10.0)
    ax.tick_params(axis="x", pad=0)

    handles = [
        Patch(
            facecolor=faces[k],
            edgecolor=edges[k],
            linewidth=1.1,
            alpha=0.88,
            label=SAR_CHANNEL_LEGEND_TEX[k],
        )
        for k in range(n_c)
    ]
    ax.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.52, 1.07),
        ncol=3,
        frameon=True,
        framealpha=0.96,
        facecolor="white",
        edgecolor="#CCCCCC",
        fontsize=9.0,
        handletextpad=0.55,
        columnspacing=1.15,
    )

    try:
        ax.set_box_aspect((xmax - xmin, ymax - ymin, (zmax - zmin) * 2.05))
    except AttributeError:
        pass

    ax.set_title("")


def plot_domain_sar(ax: plt.Axes, rows: list[dict]) -> None:
    plot_domain_channel_sar(
        ax,
        rows,
        channel_colors=RQ2_DOMAIN_SAR_CHANNEL_COLORS,
        bar_width=0.29,
        bar_edgecolor="none",
        bar_linewidth=0,
        axes_full_frame=True,
    )
    ax.set_title("")
