"""Chance-of-Success (CoS) calculations -- pure math, no database or Flask.

This module is the authoritative home of the CoS formulas. It must have NO
SQLAlchemy or Flask imports: give it plain values in, get plain values out. The
database lookups that feed Presence CoS live in ``workflow.py``.

Domain reference
----------------
CoS values are probabilities. Throughout the app a component CoS may be stored
or entered either as a decimal probability (``0.44``) or as a whole-number
percentage (``44``); helpers here normalize between the two:

- ``_cos_probability`` accepts 0-1 or 0-100 (a value > 1 is treated as a percent
  and divided by 100) and returns a 0-1 probability.
- Displayed/stored CoS results are whole-number percentage strings: the decimal
  probability is multiplied by 100 and rounded, e.g. ``0.44`` -> ``"44"``.

Reservoir CoS is model-derived from three features -- Pull-up (encoded No=0,
Semi=1, Yes=2), Amplitude Ratio and Base-Tight-Sarah -- via the approved
RandomForest model; the stored value is that model's probability-of-success as a
whole percent.

Seal CoS rule (inputs used exactly as entered, result as a whole percent):
- most-recent activity age > 0.9 -> ``activity x fracture_permeability``
  (the 0.9 activity threshold means "recently active", so the directional dip /
  azimuth / fault-confidence terms are ignored);
- activity <= 0.9 -> ``mean(dip, azimuth_vs_SHmax, fault_confidence)
  x fracture_permeability``.

Presence CoS = final Reservoir CoS x Trap CoS x Seal CoS (each normalized to a
probability first), returned as a whole percent.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Dict

from helpers import to_float_or_none

import config

try:
    import joblib
    import numpy as np
except Exception:  # pragma: no cover - exercised only when deps are absent
    joblib = None
    np = None


# ---------------------------------------------------------------------------
# Reservoir CoS (model-derived)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_reservoir_cos_model():
    """Load the approved RF model once per application process.

    The model file is deliberately external to the database so it can be
    versioned and replaced under controlled technical governance.
    """
    if joblib is None or np is None:
        raise RuntimeError("Reservoir CoS calculation requires joblib and numpy. Install the application requirements.")
    model_path = config.rf_model_path()
    if not model_path.exists():
        raise RuntimeError(f"Reservoir CoS model is not available. Place RF_model.joblib at: {model_path}")
    return joblib.load(model_path)


def _model_float(value):
    """Coerce a feature value to float, mapping blank/invalid to ``np.nan``."""
    if value is None or str(value).strip() == "":
        return np.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def _pull_up_model_value(value):
    """Map the user-facing Pull-up selection to the approved RF model encoding.

    No=0, Semi=1, Yes=2. Numeric legacy values remain valid for old records.
    """
    if value is None or str(value).strip() == "":
        return np.nan
    normalized = str(value).strip().lower()
    mapping = {"no": 0.0, "semi": 1.0, "yes": 2.0}
    if normalized in mapping:
        return mapping[normalized]
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Pull-up must be selected as No, Semi, or Yes.") from exc


def calculate_reservoir_cos_rows(raw_rows) -> str:
    """Calculate Reservoir CoS for every row using [Pull-up, Amplitude Ratio, BTS].

    Accepts a list of dicts or a JSON string of the same. Model output is stored
    as a whole-number percentage string, e.g. ``44`` for 44%. Empty feature
    values are passed as ``np.nan`` exactly as specified by the model workflow.
    Returns a compact JSON string of the rows with ``reservoir_cos_pct`` added.
    """
    if isinstance(raw_rows, str):
        try:
            rows = json.loads(raw_rows or "[]")
        except json.JSONDecodeError as exc:
            raise ValueError("Reservoir CoS rows must be valid data.") from exc
    else:
        rows = raw_rows or []
    if not isinstance(rows, list):
        raise ValueError("Reservoir CoS rows must be a list.")
    model = _load_reservoir_cos_model()
    normalized = []
    for index, item in enumerate(rows, start=1):
        row = dict(item or {})
        features = [[
            _pull_up_model_value(row.get("pull_up")),
            _model_float(row.get("amplitude_ratio")),
            _model_float(row.get("base_tight_sarah")),
        ]]
        try:
            probability = float(model.predict_proba(features)[0][1])
        except Exception as exc:
            raise ValueError(f"Reservoir CoS could not be calculated for row {index}: {exc}") from exc
        row["reservoir_cos_pct"] = str(int(round(probability * 100)))
        normalized.append(row)
    return json.dumps(normalized, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Presence CoS (Reservoir x Trap x Seal)
# ---------------------------------------------------------------------------

def _cos_probability(value, label) -> float:
    """Normalize a CoS entered/displayed as either 0-1 or 0-100 to a probability."""
    if value is None or str(value).strip() == "":
        raise ValueError(f"{label} is required to calculate Presence CoS.")
    try:
        numeric = float(str(value).strip().replace("%", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric.") from exc
    if numeric < 0 or numeric > 100:
        raise ValueError(f"{label} must be between 0 and 100%.")
    return numeric / 100.0 if numeric > 1 else numeric


def calculate_presence_cos(reservoir: str, trap: str, seal: str) -> Dict[str, str]:
    """Compute Presence CoS from the final Reservoir, Trap and Seal CoS strings.

    Each input is the already-resolved string value (blank if missing). Each may
    be a decimal probability or a whole percentage. Returns the dict of stored
    dynamic-field values, with ``presence_cos`` blank when any input is missing.
    The stored/displayed Presence CoS is a whole percentage, e.g. ``18`` for 18%.
    """
    values = {
        "presence_reservoir_cos_pct": reservoir,
        "presence_trap_cos_pct": trap,
        "presence_seal_cos_pct": seal,
    }
    if not reservoir or not trap or not seal:
        values["presence_cos"] = ""
        return values
    probability = (
        _cos_probability(reservoir, "Final Reservoir CoS")
        * _cos_probability(trap, "Trap CoS")
        * _cos_probability(seal, "Seal CoS")
    )
    values["presence_cos"] = str(int(round(probability * 100)))
    return values


# ---------------------------------------------------------------------------
# Seal CoS (formula-derived)
# ---------------------------------------------------------------------------

def _seal_number(value, label) -> float:
    """Return one numeric Seal CoS input or raise a field-specific validation error."""
    if value is None or str(value).strip() == "":
        raise ValueError(f"{label} is required to calculate Seal CoS.")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric.") from exc


def calculate_seal_cos(fields) -> str:
    """Calculate Seal CoS and return it as a whole-number percentage string.

    Rule:
    - activity > 0.9: activity x fracture permeability
    - activity <= 0.9: average(dip, azimuth vs. SHmax, fault confidence)
      x fracture permeability

    A completely blank form returns ``""`` (so a new form can be saved without a
    spurious error). Input values are used exactly as entered; the displayed
    result converts a decimal probability to a percentage (e.g. 0.44 -> ``44``).
    """
    values = fields or {}
    activity_raw = values.get("seal_recent_activity_age")
    fracture_raw = values.get("seal_fracture_permeability")
    # Allow a completely blank new form to be saved without creating a spurious error.
    inputs = [
        activity_raw,
        values.get("seal_dip"),
        values.get("seal_azimuth_vs_shmax"),
        values.get("seal_fault_level_confidence"),
        fracture_raw,
    ]
    if not any(str(value or "").strip() for value in inputs):
        return ""

    activity = _seal_number(activity_raw, "Most recent age of activity")
    fracture_permeability = _seal_number(fracture_raw, "Fracture Permeability")
    if activity > 0.9:
        seal_cos = activity * fracture_permeability
    else:
        dip = _seal_number(values.get("seal_dip"), "Dip")
        azimuth = _seal_number(values.get("seal_azimuth_vs_shmax"), "Azimuth vs. SHmax")
        fault_confidence = _seal_number(values.get("seal_fault_level_confidence"), "Fault Level of Confidence")
        seal_cos = ((dip + azimuth + fault_confidence) / 3.0) * fracture_permeability

    return str(int(round(seal_cos * 100)))


# ---------------------------------------------------------------------------
# Trap CoS (formula-derived -- STUB, formula pending)
# ---------------------------------------------------------------------------

def calculate_trap_cos(sarah_thickness_ft, sarah_quwarah_thickness_ft):
    """Calculate Trap CoS from the SARH and SARH-QWRH thicknesses (ft).

    STUB: the save-path wiring (workflow.lifecycle) and input plumbing are in
    place; the approved formula is not. Inputs are the Thickness Estimation
    step's Sarah Formation Thickness (``formation_thickness_ft``, fetched
    cross-task by the caller) and the Trap CoS step's own Sarah-Quwarah
    Thickness (``sarah_quwarah_thickness_ft``).

    Return contract (already honored by the wiring -- do not change it):
    - ``None``  means "not computed": the save path leaves the stored /
      manually entered ``trap_cos_pct`` untouched. Returned while either
      input is missing or non-numeric, and by the placeholder below.
    - a whole-number percentage string (e.g. ``"44"``) is stored as the
      task's ``trap_cos_pct``, exactly like Seal CoS's result.

    To activate: replace the TODO block with the real formula and return
    ``str(int(round(probability * 100)))``.
    """
    sarah = to_float_or_none(sarah_thickness_ft)
    sarah_quwarah = to_float_or_none(sarah_quwarah_thickness_ft)
    if sarah is None or sarah_quwarah is None:
        return None
    # TODO(formula): Trap CoS = f(sarah, sarah_quwarah) -- pending approval.
    # probability = ...
    # return str(int(round(probability * 100)))
    return None


# ---------------------------------------------------------------------------
# Initial (Lead) Resource Assessment (formula-derived -- STUB, formula pending)
# ---------------------------------------------------------------------------

def calculate_initial_resource_assessment(p90_area_km2, p10_area_km2,
                                          sarah_thickness_ft, calculation_method=""):
    """Calculate the Lead Resource Assessment PIIP trio from areas + thickness.

    STUB: the save-path wiring (workflow.lifecycle) and input plumbing are in
    place; the approved formula is not. Inputs are the Reservoir Area
    Definition step's P90/P10 areas (km²), the Thickness Estimation step's
    Sarah Formation Thickness (ft) -- all fetched cross-task by the caller --
    and the Lead Resource Assessment step's own Calculation Method selection
    (``"GRV"`` / ``"Box Model"`` / ``""``).

    Return contract (already honored by the wiring -- do not change it):
    - ``None``  means "not computed": the save path leaves the stored /
      manually entered PIIP values untouched. Returned while any numeric
      input is missing or non-numeric, and by the placeholder below.
    - a dict of the Lead Resource Assessment step's field values to store,
      e.g. ``{"lead_piip_gas_p90": "3.1", "lead_piip_gas_mean": "5.2",
      "lead_piip_gas_p10": "8.4"}`` (numbers formatted as strings; every
      returned key is written verbatim to the task's dynamic fields).

    To activate: replace the TODO block with the real formulas and return the
    dict of computed values.
    """
    p90_area = to_float_or_none(p90_area_km2)
    p10_area = to_float_or_none(p10_area_km2)
    thickness = to_float_or_none(sarah_thickness_ft)
    if p90_area is None or p10_area is None or thickness is None:
        return None
    method = str(calculation_method or "").strip()
    # TODO(formula): PIIP P90/Mean/P10 = f(p90_area, p10_area, thickness,
    # method) -- pending approval. ``method`` selects between the GRV and
    # Box Model variants ("" = not chosen yet; return None or a default).
    # return {
    #     "lead_piip_gas_p90": ...,
    #     "lead_piip_gas_mean": ...,
    #     "lead_piip_gas_p10": ...,
    # }
    del method  # placeholder only: silences the unused-variable warning
    return None


# ---------------------------------------------------------------------------
# Segment classification (portfolio quadrants)
# ---------------------------------------------------------------------------

def segment_class(ogip_value, chance_value) -> str:
    """Classify a lead/well into a portfolio quadrant from OGIP and chance.

    Thresholds: OGIP >= 10.0 is "high resource"; chance >= 50.0 is "high chance".
    Returns "Super Star" / "Risk Taker" / "Value Hunter" / "Dog", or ``""`` when
    either input is missing/non-numeric.
    """
    ogip = to_float_or_none(ogip_value)
    chance = to_float_or_none(chance_value)
    if ogip is None or chance is None:
        return ""
    high_resource = ogip >= 10.0
    high_chance = chance >= 50.0
    if high_resource and high_chance:
        return "Super Star"
    if high_resource and not high_chance:
        return "Risk Taker"
    if not high_resource and high_chance:
        return "Value Hunter"
    return "Dog"
