"""Per-run overrides for the scenario's petrophysical distributions.

The five inputs below live in ``config/scenarios.yaml`` as the approved
per-scenario assumptions, and a run normally uses them exactly as configured.
This module lets ONE run substitute its own values for them -- what the
Calculator's Advanced settings panel sends -- without touching the file or
changing anything for anybody else.

Deliberately narrow:

* Only these five parameters. GRV, area and thickness are the run's own
  inputs already; thickness in particular stays DERIVED from its P50
  (simulation.py forces normal at 0.60x/1.40x), so it is not offered here.
* Only the three distributions the sampler implements (constant, normal,
  lognormal). An unsupported name is refused with the list, rather than
  reaching ``sample_distribution`` and failing as a configuration error.
* An override REPLACES the parameter's shape rather than merging into it --
  a normal's mean/stddev must not survive underneath a lognormal override.
  Physical BOUNDS are the exception: they carry over from the configured
  parameter unless the override restates them, so a porosity override cannot
  silently escape the 0-0.40 range the scenario declares.

Nothing here is persisted. An override applies to the one calculation it was
sent with.
"""

from __future__ import annotations

from typing import Any

from .config import resolve_shared_distribution
from .exceptions import InputValidationError

# Engine parameter name -> where its configured spec comes from, plus the
# label/unit the UI needs to render a row for it. `shared` names a key in the
# scenario's `shared:` block; `scenario_key` reads straight off the scenario;
# `method_default` reads from method_defaults.<method>.
OVERRIDABLE_PARAMETERS: tuple[dict[str, Any], ...] = (
    {"name": "porosity", "label": "Porosity", "unit": "fraction", "shared": "porosity"},
    {"name": "gas_saturation", "label": "Gas saturation (Sg)", "unit": "fraction",
     "shared": "saturation"},
    {"name": "net_to_gross", "label": "Net-to-gross (NGR)", "unit": "fraction",
     "shared": "net_to_gross"},
    {"name": "geometric_factor", "label": "Geometric factor", "unit": "fraction",
     "method_default": ("area_thickness", "geometric_factor")},
    {"name": "gas_expansion_factor_1_over_bg", "label": "Gas expansion factor (1/Bg)",
     "unit": "scf/cf", "scenario_key": "gas_expansion_factor_1_over_bg"},
)

OVERRIDABLE_NAMES = frozenset(p["name"] for p in OVERRIDABLE_PARAMETERS)

SUPPORTED_DISTRIBUTIONS = ("constant", "normal", "lognormal")

# Which numeric keys each distribution actually reads. Anything else in an
# override is a mistake worth naming rather than ignoring.
_SHAPE_KEYS: dict[str, tuple[str, ...]] = {
    "constant": ("value",),
    "normal": ("mean", "stddev", "p90", "p10"),
    "lognormal": ("p90", "p10"),
}
_BOUND_KEYS = ("minimum", "maximum")
# Keys that describe the parameter rather than its shape, and so survive an
# override untouched (units for display, the GeoX tail-cap settings).
_CARRIED_KEYS = ("unit", "cap_strategy", "apply_cap_adjustment_to_sampling")


def configured_spec(config: dict[str, Any], scenario: dict[str, Any], parameter: dict[str, Any]):
    """The scenario's own spec for one overridable parameter, or None."""
    if "shared" in parameter:
        try:
            return resolve_shared_distribution(config, scenario, parameter["shared"])
        except Exception:
            return None
    if "scenario_key" in parameter:
        spec = scenario.get(parameter["scenario_key"])
        return spec if isinstance(spec, dict) else None
    method, key = parameter["method_default"]
    spec = (config.get("method_defaults", {}).get(method, {}) or {}).get(key)
    return spec if isinstance(spec, dict) else None


def describe_parameters(config: dict[str, Any], scenario: dict[str, Any]) -> list[dict[str, Any]]:
    """The five parameters with their configured values, for a UI to prefill.

    Only the keys a caller can send back are exposed, so what the panel shows
    and what it may submit are the same shape.
    """
    described: list[dict[str, Any]] = []
    for parameter in OVERRIDABLE_PARAMETERS:
        spec = configured_spec(config, scenario, parameter)
        if not spec:
            continue
        distribution = str(spec.get("distribution") or "").strip().lower()
        entry = {
            "name": parameter["name"],
            "label": parameter["label"],
            "unit": spec.get("unit") or parameter["unit"],
            "distribution": distribution,
            # Which method this parameter belongs to, so a UI can hide the
            # geometric factor while the GRV method is selected.
            "method": parameter.get("method_default", (None,))[0],
        }
        for key in _SHAPE_KEYS.get(distribution, ()) + _BOUND_KEYS:
            if key in spec:
                entry[key] = spec[key]
        described.append(entry)
    return described


def _number(name: str, key: str, raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise InputValidationError(f"{name}: {key} must be a number.")


def validate_overrides(overrides: Any) -> dict[str, dict[str, Any]]:
    """Validate a raw overrides payload and return it normalized.

    Raises InputValidationError with a message naming the parameter, so the
    caller can put it straight in front of the user.
    """
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise InputValidationError("Advanced settings must be an object keyed by parameter.")
    clean: dict[str, dict[str, Any]] = {}
    for name, spec in overrides.items():
        if name not in OVERRIDABLE_NAMES:
            raise InputValidationError(
                f"Unknown advanced parameter '{name}'. Use one of: "
                + ", ".join(sorted(OVERRIDABLE_NAMES)) + ".")
        if not isinstance(spec, dict):
            raise InputValidationError(f"{name}: advanced settings must be an object.")
        distribution = str(spec.get("distribution") or "").strip().lower()
        if distribution not in SUPPORTED_DISTRIBUTIONS:
            raise InputValidationError(
                f"{name}: distribution must be one of "
                + ", ".join(SUPPORTED_DISTRIBUTIONS) + ".")
        allowed = set(_SHAPE_KEYS[distribution]) | set(_BOUND_KEYS) | {"distribution"}
        unknown = sorted(set(spec) - allowed)
        if unknown:
            raise InputValidationError(
                f"{name}: a {distribution} distribution has no " + ", ".join(unknown) + ".")

        entry: dict[str, Any] = {"distribution": distribution}
        for key in _SHAPE_KEYS[distribution] + _BOUND_KEYS:
            if key in spec and spec[key] not in (None, ""):
                entry[key] = _number(name, key, spec[key])

        if distribution == "constant":
            if "value" not in entry:
                raise InputValidationError(f"{name}: a constant needs a value.")
        elif distribution == "normal":
            has_moments = "mean" in entry and "stddev" in entry
            has_percentiles = "p90" in entry and "p10" in entry
            if not has_moments and not has_percentiles:
                raise InputValidationError(
                    f"{name}: a normal distribution needs either mean and stddev, or P90 and P10.")
            if has_moments and entry["stddev"] <= 0:
                raise InputValidationError(f"{name}: stddev must be greater than 0.")
        else:  # lognormal
            if "p90" not in entry or "p10" not in entry:
                raise InputValidationError(f"{name}: a lognormal distribution needs P90 and P10.")
            if entry["p90"] <= 0:
                raise InputValidationError(f"{name}: a lognormal P90 must be greater than 0.")

        if "p90" in entry and "p10" in entry and entry["p90"] >= entry["p10"]:
            raise InputValidationError(f"{name}: P90 must be lower than P10.")
        if "minimum" in entry and "maximum" in entry and entry["minimum"] >= entry["maximum"]:
            raise InputValidationError(f"{name}: minimum must be lower than maximum.")
        # These five are all fractions or a positive physical ratio; none of
        # them is meaningful below zero.
        for key, number in entry.items():
            if key != "distribution" and number < 0:
                raise InputValidationError(f"{name}: {key} must not be negative.")
        clean[name] = entry
    return clean


def apply_override(base: dict[str, Any] | None, override: dict[str, Any] | None) -> dict[str, Any]:
    """Merge one validated override onto its configured spec.

    The override owns the SHAPE (distribution and its parameters); the base
    contributes the parameter's description and its physical bounds, unless
    the override restated them.
    """
    if not override:
        return base or {}
    base = base or {}
    merged: dict[str, Any] = {"distribution": override["distribution"]}
    for key in _SHAPE_KEYS[override["distribution"]]:
        if key in override:
            merged[key] = override[key]
    for key in _BOUND_KEYS:
        if key in override:
            merged[key] = override[key]
        elif key in base:
            merged[key] = base[key]
    for key in _CARRIED_KEYS:
        if key in base:
            merged[key] = base[key]
    return merged
