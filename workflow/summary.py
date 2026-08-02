"""The computed project overview + Total Chance of Success (read time).

There is no project_overview table: the /detail ``overview`` is composed from
task_dynamic_fields at read time (_OVERVIEW_READ_SOURCES in constants.py),
and the Total Chance of Success is recomputed from the Reservoir/Trap/Seal
CoS inputs on every call (Total CoS = FIRST non-blank reservoir_cos_pct x
Trap x Seal). Nothing here writes anything, so the values can never go stale.
"""
from __future__ import annotations

import json
from typing import Dict

import cos
import db

from .constants import _OVERVIEW_LEGACY_KEYS, _OVERVIEW_READ_SOURCES


def get_project_overview(session, project_id: int):
    """Compose the per-project overview dict from task inputs, at read time.

    Pure read: every value comes straight from task_dynamic_fields (one batched
    query via get_project_dynamic_field_map) through _OVERVIEW_READ_SOURCES,
    plus ``derisking`` = the computed Total Chance of Success
    (calculate_total_cos). Nothing here is stored, so the overview can never
    drift from the step inputs it summarizes.
    """
    field_map = get_project_dynamic_field_map(session, project_id)

    def first_filled(sources):
        for task_name, field_key in sources:
            value = str((field_map.get(task_name) or {}).get(field_key) or "").strip()
            if value:
                return value
        return ""

    overview = {key: first_filled(sources) for key, sources in _OVERVIEW_READ_SOURCES.items()}
    overview.update({key: "" for key in _OVERVIEW_LEGACY_KEYS})
    overview["derisking"] = total_cos_from_fields(
        (field_map.get("Reservoir CoS") or {}).get("reservoir_cos_rows"),
        first_filled([("Trap CoS", "trap_cos_pct")]),
        first_filled([("Seal CoS", "seal_cos_pct")]),
    )
    return overview


def get_project_dynamic_field_map(session, project_id: int):
    """Return {task_name: {field_key: value}} for a project's tasks.

    RETIRED-INCLUSIVE on purpose: inactive rows (steps merged away by a
    migration -- see migrations._migrate_v4_bp_step_merges) are included so
    their stored inputs stay readable under their own task_name bucket. That
    is what the surviving-first / legacy-second fallbacks read from, both
    server-side (_OVERVIEW_READ_SOURCES) and client-side (Store.allFields in
    static/js/views/detail-form.js + detail.js). Buckets are keyed by
    task_name and every reader addresses them by an explicit name, so the
    extra keys are inert for everything else.

    This map is a pure EAV read; it is NOT the step list. The rail, the
    project editor and every derived-state query take their steps from
    ``get_project_tasks`` (``is_active = 1``), so a retired step never
    reappears as a workable component.
    """
    rows = db.fetch_all(session, """
        SELECT pt.task_name, pt.sequence_no, tdf.field_key, tdf.field_value
        FROM project_tasks pt
        LEFT JOIN task_dynamic_fields tdf ON tdf.task_id = pt.task_id
        WHERE pt.project_id = :project_id
        ORDER BY pt.sequence_no, tdf.field_key
    """, {"project_id": project_id})
    data: Dict[str, Dict[str, str]] = {}
    for row in rows:
        name = row["task_name"]
        data.setdefault(name, {})
        if row["field_key"]:
            data[name][row["field_key"]] = row["field_value"] or ""
    return data


# ---------------------------------------------------------------------------
# Total Chance of Success (read-time computation) + CoS field lookups
# ---------------------------------------------------------------------------

def _task_field_value(session, project_id, task_name, field_key):
    """Return the latest stored dynamic-field value for a project's named task."""
    row = db.fetch_one(session, """
        SELECT tdf.field_value
        FROM project_tasks pt
        LEFT JOIN task_dynamic_fields tdf
          ON tdf.task_id = pt.task_id AND tdf.field_key = :field_key
        WHERE pt.project_id = :project_id AND pt.task_name = :task_name
        ORDER BY pt.task_id DESC
        LIMIT 1
    """, {"field_key": field_key, "project_id": project_id, "task_name": task_name})
    return "" if not row or row["field_value"] is None else str(row["field_value"]).strip()


def first_reservoir_cos_row_value(raw_rows_json, key):
    """Return the FIRST non-empty ``key`` value from a reservoir_cos_rows JSON.

    Pure parsing helper shared by the final-Reservoir-CoS lookup (key
    'reservoir_cos_pct') and the Portfolio seismic-block column (key
    'seismic_volume_ar_number'). Malformed/absent JSON yields ''.
    """
    if not raw_rows_json:
        return ""
    try:
        rows = json.loads(raw_rows_json)
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(rows, list):
        return ""
    for row in rows:
        value = (row or {}).get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def total_cos_from_fields(reservoir_rows_json, trap, seal):
    """Pure Total-CoS formula over already-fetched inputs; '' if any is missing.

    Total Chance of Success (the v18 replacement for the removed Presence CoS
    step) = final Reservoir CoS (FIRST non-blank row of reservoir_cos_rows) x
    Trap CoS x Seal CoS, as a whole-percent string. Shared by
    get_project_overview (per project) and reporting's batched BP-well reads so
    the formula exists in exactly one place.
    """
    reservoir = first_reservoir_cos_row_value(reservoir_rows_json, "reservoir_cos_pct")
    values = cos.calculate_presence_cos(reservoir, str(trap or "").strip(), str(seal or "").strip())
    return str(values.get("presence_cos", "") or "")


def calculate_total_cos(session, project_id):
    """Compute a project's Total Chance of Success at read time.

    Pure READ -- nothing is stored and no history is written: the value is
    recomposed from the Reservoir/Trap/Seal CoS task inputs on every call, so
    it can never go stale.
    """
    raw_rows = _task_field_value(session, project_id, "Reservoir CoS", "reservoir_cos_rows")
    trap = _task_field_value(session, project_id, "Trap CoS", "trap_cos_pct")
    seal = _task_field_value(session, project_id, "Seal CoS", "seal_cos_pct")
    return total_cos_from_fields(raw_rows, trap, seal)
