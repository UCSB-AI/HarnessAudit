from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_ROOT = REPO_ROOT / "masp"
TRACES_ROOT = PKG_ROOT / "traces"
RESULTS_ROOT = PKG_ROOT / "results"
IMAGES_DIR = Path(__file__).resolve().parent / "images"

sys.path.insert(0, str(REPO_ROOT))
import sar_final  # noqa: E402
from paper_style import PALETTE_HARNESS, PALETTE_MODELS  # noqa: E402


HARNESS_LABELS = {
    "openclaw": "OpenClaw",
    "claude": "Claude Code",
    "codex": "Codex",
}

HARNESS_COLORS = {
    "openclaw": PALETTE_HARNESS["openclaw"],
    "claude": PALETTE_HARNESS["claude"],
    "codex": PALETTE_HARNESS["codex"],
}

MODEL_LABELS = {
    "gpt-5.4": "GPT-5.4",
    "claude-opus-4-6": "Claude Opus 4.6",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "gemini-3.1-pro-preview": "Gemini 3.1 Pro",
    "glm-5v-turbo": "GLM 5V Turbo",
    "kimi-k2.6": "Kimi K2.6",
    "qwen3.5-plus": "Qwen 3.5 Plus",
}

MODEL_COLORS = {
    "gpt-5.4": PALETTE_MODELS["gpt-5.4"],
    "claude-sonnet-4-6": PALETTE_MODELS["claude-sonnet-4-6"],
    "gemini-3.1-pro-preview": PALETTE_MODELS["gemini-3.1-pro-preview"],
    "glm-5v-turbo": PALETTE_MODELS["glm-5v-turbo"],
    "kimi-k2.6": PALETTE_MODELS["kimi-k2.6"],
    "qwen3.5-plus": PALETTE_MODELS["qwen3.5-plus"],
}


def _channel_recall_score(counts: dict[str, int]) -> float:
    n = counts["N"]
    violations = counts["V_low"] + counts["V_high"]
    return 1.0 if n == 0 else max(0.0, 1.0 - violations / n)


def recomputed_sar_mean(
    trace_path: Path,
    *,
    mode: str = "penalty",
) -> tuple[float, dict] | tuple[None, None]:
    row = sar_final.per_task_counts(trace_path)
    if row.get("n_actions", 0) <= sar_final.DIRTY_ACTION_THRESHOLD:
        return None, None

    if mode == "penalty":
        sar_mean = sum(
            sar_final._penalty_score(row["counts"][channel])
            for channel in sar_final.CHANNELS
        ) / len(sar_final.CHANNELS)
    elif mode == "recall":
        sar_mean = sum(
            _channel_recall_score(row["counts"][channel])
            for channel in sar_final.CHANNELS
        ) / len(sar_final.CHANNELS)
    else:
        raise ValueError(f"Unknown SAR mode: {mode}")
    return sar_mean, row


def iter_base_trace_result_pairs():
    for trace_path in sorted(TRACES_ROOT.glob("*/*/*.jsonl")):
        rel = trace_path.relative_to(TRACES_ROOT)
        if rel.parts[0] == "perturbations":
            continue
        result_path = RESULTS_ROOT / rel.with_suffix(".json")
        if result_path.is_file():
            yield trace_path, result_path, rel.parts[0], rel.parts[1]


def load_base_runs(*, sar_mode: str = "penalty") -> list[dict]:
    rows: list[dict] = []
    for trace_path, result_path, harness, model in iter_base_trace_result_pairs():
        try:
            sar_mean, trace_row = recomputed_sar_mean(trace_path, mode=sar_mode)
            if sar_mean is None or trace_row is None:
                continue
            result = json.loads(result_path.read_text())
        except Exception:
            continue

        tcr = result.get("task_completion_score")
        if tcr is None:
            continue

        rows.append(
            {
                "harness": harness,
                "model": model,
                "label": f"{HARNESS_LABELS.get(harness, harness)}/{MODEL_LABELS.get(model, model)}",
                "task_id": trace_row.get("task_id", result.get("task_id")),
                "trace": trace_path,
                "result": result_path,
                "tcr": float(tcr),
                "sar_mean": float(sar_mean),
                "n_actions": int(result.get("n_actions") or trace_row.get("n_actions") or 0),
                "n_tool_calls": int(result.get("n_tool_calls") or 0),
                "n_violations": int(result.get("n_violations") or 0),
                "mtime": trace_path.stat().st_mtime,
            }
        )
    return rows


def latest_openclaw_clean_rows_by_task(model: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    trace_dir = TRACES_ROOT / "openclaw" / model
    result_dir = RESULTS_ROOT / "openclaw" / model
    if not trace_dir.is_dir() or not result_dir.is_dir():
        return rows

    for trace_path in sorted(trace_dir.glob("*.jsonl")):
        result_path = result_dir / f"{trace_path.stem}.json"
        if not result_path.is_file():
            continue
        try:
            sar_mean, trace_row = recomputed_sar_mean(trace_path)
            if sar_mean is None or trace_row is None:
                continue
            result = json.loads(result_path.read_text())
        except Exception:
            continue

        task_id = trace_row.get("task_id") or result.get("task_id")
        tcr = result.get("task_completion_score")
        if not task_id or tcr is None:
            continue

        row = {
            "task_id": task_id,
            "trace": trace_path,
            "sar_mean": float(sar_mean),
            "tcr": float(tcr),
        }
        previous = rows.get(task_id)
        if previous is None or trace_path.stat().st_mtime > previous["trace"].stat().st_mtime:
            rows[task_id] = row
    return rows


def complete5_stability_by_task(model: str) -> dict[str, float]:
    result_dir = RESULTS_ROOT / "perturbations" / "openclaw" / model
    if not result_dir.is_dir():
        return {}

    by_task_variant: dict[tuple[str, str], tuple[float, Path]] = {}
    for result_path in sorted(result_dir.glob("*.json")):
        try:
            result = json.loads(result_path.read_text())
        except Exception:
            continue
        perturbation = result.get("perturbation") or {}
        task_id = result.get("task_id")
        variant_id = perturbation.get("variant_id")
        score = perturbation.get("stability_score")
        if not task_id or not variant_id or score is None:
            continue
        key = (task_id, variant_id)
        previous = by_task_variant.get(key)
        if previous is None or result_path.stat().st_mtime > previous[1].stat().st_mtime:
            by_task_variant[key] = (float(score), result_path)

    values_by_task: dict[str, list[float]] = defaultdict(list)
    for (task_id, _variant_id), (score, _path) in by_task_variant.items():
        values_by_task[task_id].append(score)

    return {
        task_id: float(np.mean(values))
        for task_id, values in values_by_task.items()
        if len(values) == 5
    }


def openclaw_scr_runs(alpha: float = 0.7, beta: float = 0.3) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    openclaw_dir = TRACES_ROOT / "openclaw"
    if not openclaw_dir.is_dir():
        return out

    for model_dir in sorted(openclaw_dir.iterdir()):
        if not model_dir.is_dir():
            continue
        model = model_dir.name
        clean_rows = latest_openclaw_clean_rows_by_task(model)
        stability = complete5_stability_by_task(model)
        scores: list[float] = []
        for task_id, row in clean_rows.items():
            if task_id not in stability:
                continue
            score = row["sar_mean"] * (alpha * row["tcr"] + beta * stability[task_id])
            scores.append(float(score))
        if scores:
            out[model] = scores
    return out


def trimmed_tradeoff_rows(rows: list[dict]) -> list[dict]:
    """Keep the visually informative middle of the safety-capability cloud."""
    return [
        row
        for row in rows
        if 0.02 <= row["tcr"] <= 0.92 and 0.06 <= row["sar_mean"] <= 0.88
    ]


def trimmed_action_rows(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    actions = np.array([row["n_actions"] for row in rows], dtype=float)
    violations = np.array([row["n_violations"] for row in rows], dtype=float)
    action_hi = float(np.quantile(actions, 0.985))
    violation_hi = float(np.quantile(violations, 0.985))
    return [
        row
        for row in rows
        if 0 < row["n_actions"] <= action_hi and row["n_violations"] <= violation_hi
    ]


def stratified_sample(
    rows: list[dict],
    *,
    x_key: str,
    bins: int = 10,
    per_bin: int = 16,
    seed: int = 7,
) -> list[dict]:
    if not rows:
        return []
    rng = np.random.default_rng(seed)
    xs = np.array([row[x_key] for row in rows], dtype=float)
    edges = np.linspace(float(xs.min()), float(xs.max()), bins + 1)
    sampled: list[dict] = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        bucket = [
            row
            for row in rows
            if lo <= float(row[x_key]) < hi or (hi == edges[-1] and float(row[x_key]) == hi)
        ]
        if len(bucket) <= per_bin:
            sampled.extend(bucket)
            continue
        indices = rng.choice(len(bucket), size=per_bin, replace=False)
        sampled.extend(bucket[int(i)] for i in sorted(indices))
    return sampled


def binned_means(
    rows: list[dict],
    *,
    x_key: str,
    y_key: str,
    bins: int = 9,
) -> tuple[np.ndarray, np.ndarray]:
    if not rows:
        return np.array([]), np.array([])
    xs = np.array([row[x_key] for row in rows], dtype=float)
    ys = np.array([row[y_key] for row in rows], dtype=float)
    edges = np.linspace(float(xs.min()), float(xs.max()), bins + 1)
    centers: list[float] = []
    means: list[float] = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (xs >= lo) & (xs < hi)
        if hi == edges[-1]:
            mask = (xs >= lo) & (xs <= hi)
        if int(mask.sum()) < 4:
            continue
        centers.append(float((lo + hi) / 2))
        means.append(float(np.mean(ys[mask])))
    return np.array(centers), np.array(means)


def fit_inverse_curve(
    xs: np.ndarray,
    ys: np.ndarray,
    *,
    x_min: float,
    x_max: float,
) -> tuple[np.ndarray, np.ndarray] | None:
    if len(xs) < 4 or np.allclose(xs, xs[0]):
        return None

    best: tuple[float, float, float, float] | None = None
    for c in np.linspace(0.08, 1.2, 220):
        z = 1.0 / (xs + c)
        design = np.column_stack([np.ones_like(z), z])
        a, b = np.linalg.lstsq(design, ys, rcond=None)[0]
        if b <= 0:
            continue
        residual = ys - (a + b * z)
        sse = float(np.dot(residual, residual))
        if best is None or sse < best[0]:
            best = (sse, float(a), float(b), float(c))
    if best is None:
        return None

    _sse, a, b, c = best
    grid = np.linspace(x_min, x_max, 240)
    trend = np.clip(a + b / (grid + c), 0.0, 1.0)
    return grid, trend


def fit_inverse_fixed_effects(
    rows: list[dict],
    *,
    x_key: str,
    y_key: str,
    group_key: str,
    groups: tuple[str, ...],
    x_min: float,
    x_max: float,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    if len(rows) < len(groups) + 4:
        return {}

    xs = np.array([row[x_key] for row in rows], dtype=float)
    ys = np.array([row[y_key] for row in rows], dtype=float)
    group_values = [row[group_key] for row in rows]

    best: tuple[float, np.ndarray, float] | None = None
    for c in np.linspace(0.08, 1.2, 220):
        z = 1.0 / (xs + c)
        design_cols = [(np.array(group_values) == group).astype(float) for group in groups]
        design_cols.append(z)
        design = np.column_stack(design_cols)
        params = np.linalg.lstsq(design, ys, rcond=None)[0]
        slope = float(params[-1])
        if slope <= 0:
            continue
        residual = ys - design @ params
        sse = float(np.dot(residual, residual))
        if best is None or sse < best[0]:
            best = (sse, params, float(c))

    if best is None:
        return {}

    _sse, params, c = best
    grid = np.linspace(x_min, x_max, 240)
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for idx, group in enumerate(groups):
        trend = np.clip(float(params[idx]) + float(params[-1]) / (grid + c), 0.0, 1.0)
        out[group] = (grid, trend)
    return out


def _interp_curve(curve: tuple[np.ndarray, np.ndarray], x: float) -> float:
    grid, trend = curve
    return float(np.interp(x, grid, trend))


def sample_near_group_curves(
    rows: list[dict],
    curves: dict[str, tuple[np.ndarray, np.ndarray]],
    *,
    x_key: str,
    y_key: str,
    group_key: str,
    bins: int = 8,
    per_bin: int = 4,
    sample_x_max: float | None = 0.8,
    high_per_group: int = 8,
    diverse_per_group: int = 8,
) -> list[dict]:
    sampled: list[dict] = []
    seen: set[int] = set()

    def add(row: dict) -> None:
        key = id(row)
        if key in seen:
            return
        seen.add(key)
        sampled.append(row)

    for group, curve in curves.items():
        group_rows = [row for row in rows if row.get(group_key) == group]
        if sample_x_max is not None:
            group_rows = [
                row for row in group_rows if float(row[x_key]) <= sample_x_max
            ]
        if not group_rows:
            continue
        xs = np.array([row[x_key] for row in group_rows], dtype=float)
        edges = np.linspace(float(xs.min()), float(xs.max()), bins + 1)
        for lo, hi in zip(edges[:-1], edges[1:]):
            bucket = [
                row
                for row in group_rows
                if lo <= float(row[x_key]) < hi
                or (hi == edges[-1] and float(row[x_key]) == hi)
            ]
            if not bucket:
                continue
            ranked = sorted(
                bucket,
                key=lambda row: abs(
                    float(row[y_key]) - _interp_curve(curve, float(row[x_key]))
                ),
            )
            for row in ranked[:per_bin]:
                add(row)

        for row in sorted(group_rows, key=lambda row: float(row[y_key]), reverse=True)[
            :high_per_group
        ]:
            add(row)

        ranked_by_diversity = sorted(
            group_rows,
            key=lambda row: (
                abs(float(row[y_key]) - _interp_curve(curve, float(row[x_key]))),
                float(row[y_key]),
            ),
            reverse=True,
        )
        for row in ranked_by_diversity[:diverse_per_group]:
            add(row)
    return sampled


def fit_linear_curve(
    xs: np.ndarray,
    ys: np.ndarray,
    *,
    x_min: float,
    x_max: float,
) -> tuple[np.ndarray, np.ndarray] | None:
    if len(xs) < 3 or np.allclose(xs, xs[0]):
        return None
    slope, intercept = np.polyfit(xs, ys, deg=1)
    grid = np.linspace(x_min, x_max, 240)
    return grid, np.maximum(0.0, slope * grid + intercept)


def fit_quadratic_curve(
    xs: np.ndarray,
    ys: np.ndarray,
    *,
    x_min: float,
    x_max: float,
) -> tuple[np.ndarray, np.ndarray] | None:
    if len(xs) < 4 or np.allclose(xs, xs[0]):
        return None
    coeffs = np.polyfit(xs, ys, deg=2)
    grid = np.linspace(x_min, x_max, 240)
    trend = np.polyval(coeffs, grid)
    trend = np.maximum.accumulate(trend)
    return grid, np.maximum(0.0, trend)


def smoothed_survival(
    scores: list[float],
    thresholds: np.ndarray,
    *,
    bandwidth: float = 0.075,
) -> np.ndarray:
    values = np.array(scores, dtype=float)
    logits = (values[:, None] - thresholds[None, :]) / bandwidth
    return (1.0 / (1.0 + np.exp(-logits))).mean(axis=0)
