"""Reusable exceedance-probability plotting utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def create_exceedance_figure(result: dict[str, Any], resource: str = "gas"):
    """Create a GeoX-style exceedance figure from a calculation result."""
    samples = np.asarray(result["diagnostics"]["samples"][_sample_key(resource)], dtype=float)
    stats = result[_stats_key(resource)]
    sorted_samples = np.sort(samples)
    exceedance = 1.0 - (np.arange(1, sorted_samples.size + 1) / (sorted_samples.size + 1.0))

    fig, ax = plt.subplots(figsize=(8.4, 4.8), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.plot(sorted_samples, exceedance, color="#1f77b4", linewidth=2.2, solid_capstyle="round")
    ax.plot(sorted_samples, exceedance, color="black", linewidth=1.15, alpha=0.95, solid_capstyle="round")

    ax.set_xlabel(_x_axis_label(resource), fontsize=12)
    ax.set_ylabel("Probability", fontsize=12)
    ax.set_xlim(0.0, _nice_axis_upper(sorted_samples, stats))
    ax.set_ylim(0, 1)
    ax.set_yticks(np.linspace(0.0, 1.0, 6))
    ax.margins(x=0, y=0)
    ax.grid(True, color="#d9e8f5", linewidth=0.9)
    ax.tick_params(axis="both", labelsize=11, width=1.0, length=4)
    for spine in ax.spines.values():
        spine.set_color("black")
        spine.set_linewidth(1.1)

    annotations = [
        _annotate_stat(ax, "P90", stats["p90"], 0.90),
        _annotate_stat(ax, "P50", stats["p50"], 0.50, xytext=(10, 12)),
        _annotate_stat(ax, "P10", stats["p10"], 0.10),
    ]
    _annotate_mean_stat(
        ax,
        stats["mean"],
        _exceedance_at_value(sorted_samples, exceedance, stats["mean"]),
        _mean_box_style(resource),
        annotations,
    )
    fig.tight_layout()
    return fig


def export_exceedance_png(result: dict[str, Any], output_path: str | Path) -> Path:
    """Save the reusable exceedance figure as a PNG and return its path."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig = create_exceedance_figure(result)
    fig.savefig(path, format="png", bbox_inches="tight")
    plt.close(fig)
    return path


def _annotate_stat(
    ax,
    label: str,
    value: float,
    probability: float,
    box_style: dict[str, object] | None = None,
    xytext: tuple[int, int] = (8, 8),
) :
    bbox = box_style or {"boxstyle": "round,pad=0.18", "fc": "white", "ec": "#c8d8e8", "alpha": 0.95}
    ax.scatter([value], [probability], color="black", s=28, zorder=5)
    return ax.annotate(
        f"{label} [{value:.1f}]",
        xy=(value, probability),
        xytext=xytext,
        textcoords="offset points",
        fontsize=10,
        color="black",
        bbox=bbox,
    )


def _annotate_mean_stat(
    ax,
    value: float,
    probability: float,
    box_style: dict[str, object],
    existing_annotations: list[Any],
) -> None:
    ax.scatter([value], [probability], color="black", s=28, zorder=5)
    candidate_offsets = [(12, 14), (12, -28), (22, 28), (22, -42), (-74, 18), (-74, -34), (32, 44)]
    renderer = ax.figure.canvas.get_renderer()
    for offset in candidate_offsets:
        annotation = ax.annotate(
            f"Mean [{value:.1f}]",
            xy=(value, probability),
            xytext=offset,
            textcoords="offset points",
            fontsize=10,
            color="black",
            bbox=box_style,
        )
        ax.figure.canvas.draw()
        renderer = ax.figure.canvas.get_renderer()
        extent = annotation.get_window_extent(renderer)
        if not any(extent.overlaps(other.get_window_extent(renderer)) for other in existing_annotations):
            return
        annotation.remove()
    ax.annotate(
        f"Mean [{value:.1f}]",
        xy=(value, probability),
        xytext=(32, 44),
        textcoords="offset points",
        fontsize=10,
        color="black",
        bbox=box_style,
    )


def _exceedance_at_value(sorted_samples: np.ndarray, exceedance: np.ndarray, value: float) -> float:
    return float(np.interp(value, sorted_samples, exceedance))


def _nice_axis_upper(sorted_samples: np.ndarray, stats: dict[str, float]) -> float:
    """Return a clean dynamic x-axis upper limit for a readable display."""
    high_tail = float(np.percentile(sorted_samples, 99.5))
    display_value = max(high_tail, stats["p10"] * 1.25, stats["mean"] * 1.45)
    if display_value <= 0:
        return 1.0
    step = _nice_tick_step(display_value)
    upper = float(np.ceil(display_value / step) * step)
    return upper + (0.5 * step)


def _nice_tick_step(value: float) -> float:
    if value <= 5.0:
        return 1.0
    if value <= 20.0:
        return 5.0
    if value <= 100.0:
        return 10.0
    if value <= 500.0:
        return 50.0
    return 100.0


def _sample_key(resource: str) -> str:
    if resource == "gas":
        return "gas_bcf"
    if resource == "condensate":
        return "condensate_mmstb"
    raise ValueError(f"Unknown plotted resource '{resource}'.")


def _stats_key(resource: str) -> str:
    if resource == "gas":
        return "gas_piip"
    if resource == "condensate":
        return "condensate_piip"
    raise ValueError(f"Unknown plotted resource '{resource}'.")


def _x_axis_label(resource: str) -> str:
    if resource == "gas":
        return "Non-Associated Gas [BCF]"
    if resource == "condensate":
        return "Condensate [MMSTB]"
    return "Resource Volume"


def _mean_box_style(resource: str) -> dict[str, object]:
    if resource == "condensate":
        return {"boxstyle": "round,pad=0.18", "fc": "#e7f5e8", "ec": "#2e7d32", "lw": 1.4, "alpha": 0.98}
    return {"boxstyle": "round,pad=0.18", "fc": "#fde7e7", "ec": "#c62828", "lw": 1.4, "alpha": 0.98}
