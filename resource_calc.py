"""Resource Assessment calculator -- adapter over the vendored engine.

Charter mirrors cos.py: pure calculation glue with NO Flask and NO database
imports. Give it the pop-up calculator's API body, get back the PIIP percentiles
plus server-rendered exceedance plots (base64 PNG data URIs).

The Monte Carlo engine (``resource_engine``) does the actual math and owns every
bound check -- positivity, P90 < P10, per-method requirements. This module only:

- maps the dashboard's field names and human method labels ("GRV" / "Box Model")
  onto the engine's request dict and method ids ("grv" / "area_thickness");
- coerces the pop-up's string inputs with the dashboard's ``to_float_or_none``;
- raises a user-facing ``ValueError`` for the shape errors the engine can't see
  (blank scenario, unknown method label, a missing/non-numeric required input);
- translates the engine's own ``InputValidationError`` / ``ConfigurationError``
  into ``ValueError`` so main.py's centralized handler renders them as HTTP 400.

Determinism travels with the engine (fixed seed/iterations), so identical bodies
yield identical percentiles -- see tests/test_resource_calc.py.
"""
from __future__ import annotations

import base64
import io
import logging
import threading
from typing import Any, Dict, List

from helpers import to_float_or_none

import config
from resource_engine import (
    ConfigurationError,
    InputValidationError,
    calculate_resources,
    create_exceedance_figure,
    list_scenarios,
)

try:  # pragma: no cover - matplotlib is a hard dependency in practice
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover
    plt = None

logger = logging.getLogger(__name__)

# pyplot keeps global figure state that is NOT thread-safe: two concurrent
# requests rendering figures can corrupt each other's canvas. Every
# create -> save -> close cycle is serialized behind this module-level lock.
_PLOT_LOCK = threading.Lock()

# The pop-up sends the human method label; the engine speaks method ids.
_METHOD_LABELS = {"GRV": "grv", "Box Model": "area_thickness"}


def scenario_options() -> List[Dict[str, str]]:
    """Return the selectable scenarios for the pop-up's dropdown.

    Only scenarios with ``status == "configured"`` are offered. Each entry is
    ``{"id", "label", "resource_type"}`` (label = the scenario's display_name).
    A missing/broken scenarios file must never take the whole /api/meta response
    down, so a ConfigurationError degrades to an empty list plus a logged warning.
    """
    try:
        summaries = list_scenarios(config.resource_scenarios_path())
    except ConfigurationError as exc:
        logger.warning("Resource scenarios are unavailable: %s", exc)
        return []
    return [
        {"id": s.scenario_id, "label": s.display_name, "resource_type": s.resource_type}
        for s in summaries
        if s.status == "configured"
    ]


def _required_number(payload: dict, key: str, label: str) -> float:
    """Coerce a required pop-up input to float or raise a field-specific error."""
    value = to_float_or_none(payload.get(key))
    if value is None:
        raise ValueError(f"{label} must be numeric.")
    return value


def build_request(payload: dict) -> Dict[str, Any]:
    """Map the pop-up's API body onto an engine request dict.

    Body shape: ``{scenario, method, grv_p90, grv_p10, area_p90_km2,
    area_p10_km2, thickness_p50_ft}`` where ``method`` is the dashboard label
    "GRV" or "Box Model". Only the fields the chosen method needs are read.

    Raises ``ValueError`` for the errors the engine can't diagnose from an
    already-built request: a blank scenario, a method that is not one of the two
    labels, or a required per-method input that is missing/non-numeric. The
    engine's own bound checks (positivity, P90 < P10) are deliberately NOT
    duplicated here -- ``run`` lets the engine validate and translates its errors.
    """
    payload = payload or {}
    scenario = str(payload.get("scenario") or "").strip()
    if not scenario:
        raise ValueError("A scenario must be selected.")

    method_label = str(payload.get("method") or "").strip()
    if method_label not in _METHOD_LABELS:
        raise ValueError('Calculation method must be "GRV" or "Box Model".')
    method = _METHOD_LABELS[method_label]

    request: Dict[str, Any] = {"scenario": scenario, "method": method}
    if method == "grv":
        request["grv_p90_thousand_acre_ft"] = _required_number(
            payload, "grv_p90", "GRV P90 [10³ acre-ft]")
        request["grv_p10_thousand_acre_ft"] = _required_number(
            payload, "grv_p10", "GRV P10 [10³ acre-ft]")
    else:
        request["area_p90_km2"] = _required_number(payload, "area_p90_km2", "Area P90 [km²]")
        request["area_p10_km2"] = _required_number(payload, "area_p10_km2", "Area P10 [km²]")
        request["thickness_p50_ft"] = _required_number(
            payload, "thickness_p50_ft", "Thickness P50 [ft]")
    return request


def _render_plot(result: dict, resource: str) -> str:
    """Render one exceedance figure to a base64 PNG ``data:`` URI.

    The whole create/save/close cycle runs under ``_PLOT_LOCK`` because pyplot's
    figure registry is process-global and not thread-safe.
    """
    with _PLOT_LOCK:
        fig = create_exceedance_figure(result, resource=resource)
        try:
            buffer = io.BytesIO()
            fig.savefig(buffer, format="png", dpi=110, bbox_inches="tight")
        finally:
            if plt is not None:
                plt.close(fig)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def run(payload: dict) -> Dict[str, Any]:
    """Run the Monte Carlo engine for a pop-up body and package the response.

    Returns ``{"gas": {...}, "condensate"?: {...}, "units": {...},
    "plots": {"gas": <data uri>, "condensate"?: <data uri>}}``. The gas plot is
    always rendered; the condensate percentiles and plot appear only when the
    scenario produces ``condensate_piip``. Engine validation/config failures are
    re-raised as ``ValueError`` for the dashboard's 400 handling.
    """
    request = build_request(payload)
    try:
        result = calculate_resources(request, config_path=str(config.resource_scenarios_path()))
    except (InputValidationError, ConfigurationError) as exc:
        raise ValueError(str(exc)) from exc

    response: Dict[str, Any] = {
        "gas": result["gas_piip"],
        "units": result["units"],
        "plots": {"gas": _render_plot(result, "gas")},
    }
    if result.get("condensate_piip"):
        response["condensate"] = result["condensate_piip"]
        response["plots"]["condensate"] = _render_plot(result, "condensate")
    return response


def format_stored(value: float) -> str:
    """Format a PIIP number for storage/display, mirroring the frontend rule.

    ``.2f`` below 10, ``.1f`` in [10, 1000), ``.0f`` at/above 1000 (magnitude,
    like the JS ``formatStored``). Exported so the backend and the pop-up's JS
    render identical strings over the PIIP range (parity tested).
    """
    numeric = float(value)
    if abs(numeric) < 10:
        return f"{numeric:.2f}"
    if abs(numeric) < 1000:
        return f"{numeric:.1f}"
    return f"{numeric:.0f}"
