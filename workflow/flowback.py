"""Canonical Flowback-stage row helpers.

Flowback Results has two intentionally different storage layers: the retired
flat task fields retain their ``flowback_*`` names, while every row in the
``flowback_stages_rows`` JSON mini-sheet uses the concise names below.  Older
standard-editor and import data used the flat names inside stage rows as well;
these helpers make that historical shape safe to read while all current
writers converge on the row schema.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional


# The stable, persisted names for one entry in flowback_stages_rows.  ``id``
# is deliberately separate: it is an opaque repeatable-row identity, not a
# measured flowback field.
FLOWBACK_STAGE_FIELDS = (
    "formation", "top_md", "base_md", "dynamic_area_km2", "dynamic_ogip_bcf",
    "gas_rate_mmscfd", "water_rate_bwpd", "liquid_rate_bpd", "choke_size_in", "fwhp_psi",
)

# The legacy aliases existed only inside historical stage rows.  Flat task
# fields keep these names permanently, so callers must not apply this map to a
# task field dictionary.
FLOWBACK_STAGE_ALIASES = {
    "formation": "flowback_formation",
    "top_md": "flowback_top_md",
    "base_md": "flowback_base_md",
    "gas_rate_mmscfd": "flowback_gas_rate_mmscfd",
    "water_rate_bwpd": "flowback_water_rate_bwpd",
    "liquid_rate_bpd": "flowback_liquid_rate_bpd",
    "choke_size_in": "flowback_choke_size_in",
    "fwhp_psi": "flowback_fwhp_psi",
}

# A depth/formation-only row identifies an interval but is not a result.  The
# same predicate drives every surface that chooses one headline stage.
FLOWBACK_MEASUREMENT_FIELDS = (
    "gas_rate_mmscfd", "water_rate_bwpd", "liquid_rate_bpd", "choke_size_in", "fwhp_psi",
)


def is_filled(value: Any) -> bool:
    """Whether ``value`` is a non-blank stage value."""
    return value is not None and str(value).strip() != ""


def normalize_flowback_stage(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return a canonical row while retaining IDs and unknown future fields.

    A populated canonical field wins a populated legacy alias.  A blank or
    absent canonical field is filled from its alias.  Aliases are removed from
    the returned row so a subsequent save cannot perpetuate two names for the
    same measurement.
    """
    source = row if isinstance(row, dict) else {}
    normalized = dict(source)
    if not is_filled(normalized.get("id")) and is_filled(normalized.get("_id")):
        normalized["id"] = normalized["_id"]
    normalized.pop("_id", None)
    for canonical, legacy in FLOWBACK_STAGE_ALIASES.items():
        if not is_filled(normalized.get(canonical)) and is_filled(source.get(legacy)):
            normalized[canonical] = source.get(legacy)
        normalized.pop(legacy, None)
    return normalized


def normalize_flowback_rows(rows: Iterable[Any]) -> List[Any]:
    """Normalize dict rows without losing malformed historical list members."""
    return [normalize_flowback_stage(row) if isinstance(row, dict) else row for row in rows]


def parse_flowback_rows(raw: Any) -> List[Any]:
    """Parse a stage blob tolerantly, returning an empty list on bad JSON."""
    if isinstance(raw, list):
        rows = raw
    else:
        try:
            rows = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return []
    return normalize_flowback_rows(rows) if isinstance(rows, list) else []


def stage_has_measurement(row: Any) -> bool:
    """True for a stage containing at least one headline measurement."""
    return isinstance(row, dict) and any(is_filled(row.get(key)) for key in FLOWBACK_MEASUREMENT_FIELDS)


def primary_flowback_stage(raw: Any) -> Optional[Dict[str, Any]]:
    """Return the first measured canonical stage, or ``None`` when absent."""
    for row in parse_flowback_rows(raw):
        if stage_has_measurement(row):
            return row
    return None
