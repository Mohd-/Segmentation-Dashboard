"""Auto-fill governed values derived from well location and BP inputs.

Two fills share one mechanism -- resolve the project's coordinates, sample a
configured ZMAP+ surface (surfaces.py) there, store the result:

- :func:`fill_tsq` samples the SARH-QWRH thickness ("TSQ") surface and fills
  the Trap and Seal CoS step's ``sarah_quwarah_thickness_ft`` field, but ONLY
  while that field is empty -- a manual entry always wins and is never
  overwritten. The write goes through the same task_dynamic_fields upsert every
  field save uses, with a task_history note beside it, so the value is
  indistinguishable in storage from a typed one (and auditable as auto-filled).
- :func:`fill_ground_elevation` samples the digital-elevation surface into
  ``projects.ground_elevation``. That column is MACHINE-DERIVED, so overwriting
  is always correct; it is only left untouched when there is nothing to say
  (no coordinates, no surface, or no value at the point).
- :func:`fill_bp_calculations` owns the BP Gate TD and drilling-days outputs,
  preserving any legacy user-entered value as provenance before replacing it.

Coordinate precedence mirrors workflow/mapdata.py exactly: the STAKED pair
(``staked_x``/``staked_y`` in task_dynamic_fields, both present and numeric)
supersedes the planned ``projects.lead_x``/``lead_y`` pair, and half a pair is
not a location. The staked fold is retired-inclusive with active rows winning,
reproduced from mapdata._staked_coordinates for the single-project case.

Both fills are cheap no-ops when their surface file is not configured/present,
and a corrupt surface can never raise out of them (surfaces.sample_surface's
contract) -- safe to call after ANY save.

Session semantics: like history.log_task_event and lifecycle's helpers, nothing
here commits or opens a transaction -- the CALLER owns the transaction (the
save path that triggered the fill, or scripts/backfill_surfaces.py's
write_transaction blocks).

Import direction: lifecycle.py will call INTO this module (the save-time
wiring); this module must therefore never import lifecycle.
"""
from __future__ import annotations

import json
import logging
import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple

import config
import db
import surfaces
from helpers import utc_now_str

from .history import log_task_event
from .mapdata import _coordinate_pair

logger = logging.getLogger(__name__)

# The step and field the TSQ surface feeds. Field-keyed writes would survive a
# step rename only if the row does too, so the ACTIVE row is looked up by the
# current step name (the same name lifecycle's recompute hooks key on).
TSQ_TASK_NAME = "Trap and Seal CoS"
TSQ_FIELD_KEY = "sarah_quwarah_thickness_ft"

BP_GATE_TASK_NAME = "BP Execution Gate"
LEAD_ASSESSMENT_TASK_NAME = "Lead Assessment"
BP_TD_PROGNOSIS_FIELD_KEY = "sarh_formation_prognosis_pre_drill"
BP_TD_FIELD_KEY = "bp_gate_calculated_td_ft_md"
BP_DAYS_FIELD_KEY = "bp_gate_calculated_drilling_days"
BP_TD_SOURCE_KEY = "bp_gate_calculated_td_source"
BP_TD_REASON_KEY = "bp_gate_calculated_td_override_reason"
BP_TD_METADATA_KEY = "bp_gate_calculated_td_metadata"
BP_DAYS_SOURCE_KEY = "bp_gate_calculated_drilling_days_source"
BP_DAYS_METADATA_KEY = "bp_gate_calculated_drilling_days_metadata"
BP_CALCULATION_SOURCE = "System calculation"

# The audit-trail actor for auto-filled values (the migration-actor idiom).
SURFACE_FILL_ACTOR = "System (surface auto-fill)"
BP_CALCULATION_ACTOR = "System (BP calculation)"

# The staked location keys, read by KEY across active AND retired rows -- the
# same reason mapdata reads them that way: a value answers to its key whichever
# step row carries it, so a step rename/merge cannot strand the coordinates.
_STAKED_COORD_KEYS = ["staked_x", "staked_y"]


def _resolve_coordinates(session, project_id) -> Optional[Tuple[float, float]]:
    """The (x, y) a fill should sample at, or None when the project has none.

    mapdata.map_wells' precedence, for one project: the staked pair wins when
    BOTH halves parse as numbers, else the lead pair, else None. The staked
    fold is retired-inclusive with ``ORDER BY pt.is_active`` putting inactive
    rows first, so an active row's non-blank value folds in last and wins while
    a legacy value on a retired row still resolves (first-non-blank-wins,
    mapdata._staked_coordinates' rule).
    """
    rows = db.fetch_all(session, """
        SELECT tdf.field_key, tdf.field_value
        FROM project_tasks pt
        JOIN task_dynamic_fields tdf ON tdf.task_id = pt.task_id
        WHERE pt.project_id = :project_id
          AND tdf.field_key IN :field_keys
        ORDER BY pt.is_active, pt.task_id
    """, {"project_id": project_id, "field_keys": _STAKED_COORD_KEYS})
    staked = {}
    for row in rows:
        value = row["field_value"] or ""
        if value or row["field_key"] not in staked:
            staked[row["field_key"]] = value
    position = _coordinate_pair(staked.get("staked_x"), staked.get("staked_y"))
    if position is not None:
        return position
    project = db.fetch_one(session,
                           "SELECT lead_x, lead_y FROM projects WHERE project_id = :project_id",
                           {"project_id": project_id})
    if not project:
        return None
    return _coordinate_pair(project.get("lead_x"), project.get("lead_y"))


def _format_field_value(value) -> str:
    """A sampled float as the string a user might have typed: two decimals,
    trailing zeros (and a bare point) trimmed -- '30', not '30.00'."""
    text_value = "{:.2f}".format(float(value))
    if "." in text_value:
        text_value = text_value.rstrip("0").rstrip(".")
    return text_value or "0"


def fill_tsq(session, project_id) -> Optional[float]:
    """Auto-fill the Trap and Seal CoS SARH-QWRH thickness from the TSQ surface.

    Returns the sampled value WHEN IT WAS WRITTEN, else None -- and it is only
    written when every gate passes: the surface file exists, the project
    resolves coordinates, the active step row exists, its
    ``sarah_quwarah_thickness_ft`` is empty/absent (a manual entry always
    wins), and the surface actually has a value at the point. No commit; the
    caller owns the transaction.
    """
    surface_path = config.tsq_surface_file()
    if not surface_path.is_file():
        return None                     # unconfigured: no-op before any query
    task = db.fetch_one(session, """
        SELECT task_id FROM project_tasks
        WHERE project_id = :project_id AND task_name = :task_name AND is_active = 1
        ORDER BY task_id DESC
        LIMIT 1
    """, {"project_id": project_id, "task_name": TSQ_TASK_NAME})
    if not task:
        return None                     # e.g. a BP-era record without the step
    existing = db.fetch_one(session, """
        SELECT field_value FROM task_dynamic_fields
        WHERE task_id = :task_id AND field_key = :field_key
    """, {"task_id": task["task_id"], "field_key": TSQ_FIELD_KEY})
    if existing is not None and str(existing["field_value"] or "").strip():
        return None                     # manual (or earlier auto) entry wins
    position = _resolve_coordinates(session, project_id)
    if position is None:
        return None
    value = surfaces.sample_surface(surface_path, position[0], position[1])
    if value is None:
        return None

    stored = _format_field_value(value)
    now = utc_now_str()
    # The exact upsert every field save uses (lifecycle._apply_dynamic_fields),
    # so the stored shape is identical to a typed value's.
    db.execute(session, """
        INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
        VALUES (:task_id, :field_key, :field_value, :now)
        ON CONFLICT(task_id, field_key) DO UPDATE
        SET field_value = excluded.field_value, updated_at = excluded.updated_at
    """, {"task_id": task["task_id"], "field_key": TSQ_FIELD_KEY,
          "field_value": stored, "now": now})
    db.execute(session, "UPDATE project_tasks SET last_updated = :now WHERE task_id = :task_id",
               {"now": now, "task_id": task["task_id"]})
    log_task_event(session, task["task_id"], project_id, TSQ_TASK_NAME,
                   "Component Inputs Updated", None, None, SURFACE_FILL_ACTOR,
                   "Auto-filled from TSQ surface: sarah quwarah thickness ft = {}.".format(stored))
    return value


def fill_ground_elevation(session, project_id) -> Optional[float]:
    """Sample the DEM at the project's coordinates into projects.ground_elevation.

    Overwriting is fine -- the column is machine-derived, so the surface is
    always right about itself. Returns the value written, or None (leaving any
    existing stored value untouched) when the project has no coordinates, the
    surface is not configured/present, or it has no value at the point. No
    commit; the caller owns the transaction.
    """
    surface_path = config.ground_elevation_surface_file()
    if not surface_path.is_file():
        return None                     # unconfigured: no-op before any query
    position = _resolve_coordinates(session, project_id)
    if position is None:
        return None
    value = surfaces.sample_surface(surface_path, position[0], position[1])
    if value is None:
        return None
    db.execute(session, """
        UPDATE projects SET ground_elevation = :ground_elevation
        WHERE project_id = :project_id
    """, {"ground_elevation": float(value), "project_id": project_id})
    return value


def _whole_number(value) -> str:
    """Round a configured/sampled value half-up to the governed whole unit."""
    return str(int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def _bp_gate_task(session, project_id):
    return db.fetch_one(session, """
        SELECT pt.task_id, pt.project_id, pt.task_name
        FROM project_tasks pt
        JOIN projects p ON p.project_id = pt.project_id
        WHERE pt.project_id = :project_id
          AND pt.task_name = :task_name
          AND pt.is_active = 1
          AND p.archived = 0
          AND (p.business_plan_enabled = 1 OR p.pipeline_type = 'bp')
        ORDER BY pt.task_id DESC
        LIMIT 1
    """, {"project_id": project_id, "task_name": BP_GATE_TASK_NAME})


def _lead_assessment_task(session, project_id):
    return db.fetch_one(session, """
        SELECT pt.task_id
        FROM project_tasks pt
        JOIN projects p ON p.project_id = pt.project_id
        WHERE pt.project_id = :project_id
          AND pt.task_name = :task_name
          AND pt.is_active = 1
          AND p.archived = 0
        ORDER BY pt.task_id DESC
        LIMIT 1
    """, {"project_id": project_id, "task_name": LEAD_ASSESSMENT_TASK_NAME})


def _task_fields(session, task_id):
    return {
        row["field_key"]: "" if row.get("field_value") is None else str(row["field_value"])
        for row in db.fetch_all(session, """
            SELECT field_key, field_value FROM task_dynamic_fields
            WHERE task_id = :task_id
        """, {"task_id": task_id})
    }


def _metadata(value):
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _put_field(session, task_id, key, value, now):
    """Upsert one EAV value and report whether its stored representation moved."""
    value = "" if value is None else str(value)
    existing = db.fetch_one(session, """
        SELECT field_value FROM task_dynamic_fields
        WHERE task_id = :task_id AND field_key = :field_key
    """, {"task_id": task_id, "field_key": key})
    old = "" if not existing or existing.get("field_value") is None else str(existing["field_value"])
    if old == value and existing is not None:
        return False
    db.execute(session, """
        INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
        VALUES (:task_id, :field_key, :field_value, :now)
        ON CONFLICT(task_id, field_key) DO UPDATE
        SET field_value = excluded.field_value, updated_at = excluded.updated_at
    """, {"task_id": task_id, "field_key": key, "field_value": value, "now": now})
    return True


def _legacy_snapshot(fields, value_key, source_key, reason_key, prior_metadata):
    """Carry the first non-system calculated value forward as read-only history."""
    legacy = prior_metadata.get("legacy")
    if isinstance(legacy, dict):
        return legacy
    old_value = str(fields.get(value_key) or "").strip()
    old_source = str(fields.get(source_key) or "").strip()
    if not old_value or old_source == BP_CALCULATION_SOURCE:
        return None
    snapshot = {"value": old_value, "source": old_source or "Legacy/imported value"}
    old_reason = str(fields.get(reason_key) or "").strip() if reason_key else ""
    if old_reason:
        snapshot["reason"] = old_reason
    return snapshot


def _calculation_metadata(status, formula, inputs, unavailable_reason=None, legacy=None):
    result = {
        "status": status,
        "source": BP_CALCULATION_SOURCE,
        "formula": formula,
        "inputs": inputs,
    }
    if unavailable_reason:
        result["unavailable_reason"] = unavailable_reason
    if legacy:
        result["legacy"] = legacy
    return result


def fill_bp_calculations(session, project_id):
    """Recompute both server-owned BP Gate outputs from current dependencies.

    TD uses the active Lead Assessment SARH prognosis and the DEM grid at the
    resolved well coordinates. Drilling days uses the configured classification
    baseline and coring uplift. Missing inputs/configuration clear the active
    governed value so a stale legacy number can never masquerade as a current
    calculation.
    Legacy/imported values are preserved once in the calculation metadata.

    The caller owns the write transaction. The return value is ``None`` for a
    non-BP record and otherwise contains the two published metadata objects.
    """
    task = _bp_gate_task(session, project_id)
    if not task:
        return None
    fields = _task_fields(session, task["task_id"])
    settings = config.bp_calculations()
    position = _resolve_coordinates(session, project_id)
    lead_assessment = _lead_assessment_task(session, project_id)
    lead_fields = (_task_fields(session, lead_assessment["task_id"])
                   if lead_assessment else {})
    prognosis = None
    prognosis_raw = lead_fields.get(BP_TD_PROGNOSIS_FIELD_KEY, "")
    try:
        if str(prognosis_raw).strip():
            prognosis = float(prognosis_raw)
        if prognosis is None or not math.isfinite(prognosis):
            raise ValueError
    except (TypeError, ValueError):
        prognosis = None

    td_prior = _metadata(fields.get(BP_TD_METADATA_KEY))
    td_legacy = _legacy_snapshot(
        fields, BP_TD_FIELD_KEY, BP_TD_SOURCE_KEY, BP_TD_REASON_KEY, td_prior)
    td_inputs = {
        "base_ft": settings.get("td_base_ft") if settings else None,
        "x": position[0] if position else None,
        "y": position[1] if position else None,
        BP_TD_PROGNOSIS_FIELD_KEY: prognosis,
        "digital_elevation_ft": None,
    }
    td_missing = []
    if settings is None:
        td_missing.append("calculation configuration")
    if prognosis is None:
        td_missing.append("Lead Assessment SARH prognosis")
    if position is None:
        td_missing.append("well coordinates")
    elevation_path = config.ground_elevation_surface_file()
    if not elevation_path.is_file():
        td_missing.append("digital elevation surface")
    if not td_missing:
        elevation = surfaces.sample_surface(elevation_path, position[0], position[1])
        td_inputs["digital_elevation_ft"] = elevation
        if elevation is None or not math.isfinite(float(elevation)):
            td_inputs["digital_elevation_ft"] = None
            td_missing.append("digital elevation at well location")
    td_value = ""
    if not td_missing:
        td_value = _whole_number(settings["td_base_ft"] + prognosis + elevation)
    td_meta = _calculation_metadata(
        "calculated" if td_value else "unavailable",
        "TD base + Lead Assessment.sarh_formation_prognosis_pre_drill + digital elevation at well X/Y",
        td_inputs, ", ".join(td_missing) if td_missing else None, td_legacy)

    days_prior = _metadata(fields.get(BP_DAYS_METADATA_KEY))
    days_legacy = _legacy_snapshot(
        fields, BP_DAYS_FIELD_KEY, BP_DAYS_SOURCE_KEY, None, days_prior)
    classification = str(fields.get("bp_gate_classification") or "").strip()
    coring = str(fields.get("bp_gate_coring_program") or "").strip()
    days_inputs = {
        "classification": classification or None,
        "classification_days": None,
        "coring_program": coring or None,
        "coring_uplift_days": settings.get("coring_uplift_days") if settings else None,
    }
    days_missing = []
    if settings is None:
        days_missing.append("calculation configuration")
    elif classification not in settings["classification_days"]:
        days_missing.append("well classification")
    else:
        days_inputs["classification_days"] = settings["classification_days"][classification]
    if coring not in {"Yes", "No"}:
        days_missing.append("Coring Program")
    days_value = ""
    if not days_missing:
        total_days = days_inputs["classification_days"]
        if coring == "Yes":
            total_days += settings["coring_uplift_days"]
        days_value = _whole_number(total_days)
    days_meta = _calculation_metadata(
        "calculated" if days_value else "unavailable",
        "classification baseline + coring uplift when Coring Program is Yes",
        days_inputs, ", ".join(days_missing) if days_missing else None, days_legacy)

    now = utc_now_str()
    changes = []
    td_meta_text = json.dumps(td_meta, sort_keys=True, separators=(",", ":"))
    days_meta_text = json.dumps(days_meta, sort_keys=True, separators=(",", ":"))
    for key, value in (
        (BP_TD_FIELD_KEY, td_value),
        (BP_TD_SOURCE_KEY, BP_CALCULATION_SOURCE if td_value else ""),
        (BP_TD_REASON_KEY, ""),
        (BP_TD_METADATA_KEY, td_meta_text),
        (BP_DAYS_FIELD_KEY, days_value),
        (BP_DAYS_SOURCE_KEY, BP_CALCULATION_SOURCE if days_value else ""),
        (BP_DAYS_METADATA_KEY, days_meta_text),
    ):
        if _put_field(session, task["task_id"], key, value, now):
            changes.append(key)

    if changes:
        db.execute(session, """
            UPDATE project_tasks SET last_updated = :now, revision = revision + 1
            WHERE task_id = :task_id
        """, {"now": now, "task_id": task["task_id"]})
        db.execute(session, """
            UPDATE projects SET last_updated = :now, revision = revision + 1
            WHERE project_id = :project_id
        """, {"now": now, "project_id": project_id})
        log_task_event(
            session, task["task_id"], project_id, BP_GATE_TASK_NAME,
            "Business Plan Calculation Updated",
            fields.get(BP_TD_FIELD_KEY) or fields.get(BP_DAYS_FIELD_KEY) or None,
            td_value or days_value or None, BP_CALCULATION_ACTOR,
            json.dumps({"changed_fields": changes, "td": td_meta, "days": days_meta},
                       sort_keys=True, separators=(",", ":")),
        )
    return {"td": td_meta, "days": days_meta}


def bp_calculation_metadata(fields):
    """Calculation provenance for the detail API, including a safe fallback."""
    result = {}
    for public_key, metadata_key, formula in (
        (BP_TD_FIELD_KEY, BP_TD_METADATA_KEY,
         "TD base + Lead Assessment.sarh_formation_prognosis_pre_drill + digital elevation at well X/Y"),
        (BP_DAYS_FIELD_KEY, BP_DAYS_METADATA_KEY,
         "classification baseline + coring uplift when Coring Program is Yes"),
    ):
        metadata = _metadata(fields.get(metadata_key))
        if not metadata:
            metadata = _calculation_metadata(
                "unavailable", formula, {}, "calculation has not run")
        metadata["value"] = str(fields.get(public_key) or "")
        result[public_key] = metadata
    return result
