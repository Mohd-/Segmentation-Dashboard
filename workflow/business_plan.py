"""Approved ASAS Business Plan Execution projection and write model.

The persisted workflow keeps its stable task names and identifiers.  This
module maps those rows onto the approved three-stage, eighteen-tracking-item
experience and owns the cross-step rules that do not belong in the generic
prospect lifecycle.
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import config
import db
from helpers import today_str, utc_now_str

from .constants import (
    FORMATIONS, STAKED_WELL_NAME_FIELD, StaleRevisionError, display_record_name,
    staking_confirmed,
)
from .history import log_task_event
from .notifications import notify_transition
from .projects import _sync_completed_at
from .promotion import get_lead_summary_snapshot
from .summary import get_project_overview


FLUIDS = (
    "Gas", "Gas over Water", "Water Bearing", "Dry Hole", "Oil",
    "Oil over Gas", "Oil over Water",
)
PRODUCTIVE_FLUIDS = frozenset({"Gas", "Gas over Water", "Oil", "Oil over Gas", "Oil over Water"})
NON_PRODUCTIVE_FLUIDS = frozenset({"Water Bearing", "Dry Hole"})
# The pre-v10 spellings. Numbered migration v10 maps them, but a database that
# has not run it yet still serves them to the editor, so a full-row save has to
# be able to round-trip one UNCHANGED. They are never writable as a new value
# (see save_formations) and never appear in the select's options.
LEGACY_FLUIDS = frozenset({"Dry", "Water", "Condensate", "Liquid"})
CLASSIFICATIONS = ("Development", "Appraisal", "Exploration")
LOGGING_PROGRAMS = ("Standard A", "Standard B", "Optimized Standard B")
PRIORITIES = ("Low", "Medium", "High")

# The Business Plan Year filter's "show every year" sentinel. It is a string on
# purpose: the filter's other values arrive from the query string as strings
# too, so there is one comparison and no int/str ambiguity.
ALL_YEARS = "all"


STAGES = (
    {
        "key": "pre_drilling",
        "label": "Pre-Drilling",
        "stored_stage": "Well Delivery",
        "details": (
            ("business-plan-gate", "Business Plan Execution Gate", "BP Execution Gate"),
            ("well-letters", "Well Letters", "Well Proposal"),
            ("gheer-inputs", "GHEER Inputs", "GHEER"),
        ),
        "items": (
            ("business-plan-gate", "Business Plan Gate", "business-plan-gate"),
            ("well-proposal", "Well Proposal", "well-letters"),
            ("site-preparation", "Site Preparation", "well-letters"),
            ("approval-to-drill", "Approval to Drill", "well-letters"),
            ("gheer-geophysics", "GHEER: Geophysics", "gheer-inputs"),
            ("gheer-geomechanics", "GHEER: Geomechanics", "gheer-inputs"),
        ),
    },
    {
        "key": "post_drilling",
        "label": "Post-Drilling",
        "stored_stage": "Post-Drilling",
        "details": (
            ("quicklook-logs", "Quicklook Logs", "Quicklook Logs"),
            # Card 3A spells the visible label "Picks". The slug and the stored
            # task name ("Aramco Picks") are identifiers and stay put -- only
            # the middle element of this tuple reaches a screen.
            ("aramco-approved-pics", "Aramco Approved Picks", "Aramco Picks"),
            ("sad-model", "SAD Model", "SAD Model"),
            ("summary-slides", "Summary Slides", "Executive Summary"),
            ("post-drill-learning-review", "Post-Drill Learning Review", "Post-Well Outcome & Decision Gate"),
        ),
        "items": (
            ("quicklook-logs", "Quicklook Logs", "quicklook-logs"),
            ("aap", "AAP", "aramco-approved-pics"),
            ("sad-model", "SAD Model", "sad-model"),
            ("executive-summary", "Executive Summary", "summary-slides"),
            ("ured-update", "URED Update", "summary-slides"),
            ("learnings", "Learnings", "post-drill-learning-review"),
        ),
    },
    {
        "key": "post_testing",
        "label": "Post-Testing",
        "stored_stage": "Post-Testing",
        "details": (
            ("flowback-results", "Flowback Results", "Flowback Results"),
            ("sad-model-update", "SAD Model Update", "SAD Update"),
            ("final-summary-slides", "Final Summary Slides", "SAD Update"),
            ("final-log-analysis", "Final Log Analysis", "Final Log Analysis"),
            ("structural-mtr", "Structural MTR", "PVAD Structural MTR"),
            ("pda-booking", "Post-Drilling Analysis & Reserves Booking", "PDA"),
        ),
        "items": (
            ("flowback", "Flowback", "flowback-results"),
            ("sad-update", "SAD Update", "sad-model-update"),
            ("final-summary", "Final Summary", "final-summary-slides"),
            ("final-logs", "Final Logs", "final-log-analysis"),
            ("mtr", "MTR", "structural-mtr"),
            ("pda-booking", "PDA & Booking", "pda-booking"),
        ),
    },
)

DETAILS = {
    slug: {
        "slug": slug,
        "label": label,
        "task_name": task_name,
        "stage_key": stage["key"],
        "stage_label": stage["label"],
    }
    for stage in STAGES for slug, label, task_name in stage["details"]
}

TASK_NAMES = tuple({task for stage in STAGES for _slug, _label, task in stage["details"]}
                   | {"Site Preparation", "Approval To Drill"})

STEP_OPTIONS = tuple(
    {"value": key, "label": label, "stage_key": stage["key"]}
    for stage in STAGES for key, label, _detail in stage["items"]
)

ITEM_TASK_NAMES = {
    key: task_name
    for stage in STAGES
    for key, _label, detail_slug in stage["items"]
    for slug, _detail_label, task_name in stage["details"]
    if slug == detail_slug
}
ITEM_STAGE_KEYS = {
    key: stage["key"] for stage in STAGES for key, _label, _detail_slug in stage["items"]
}

APPROVAL_DETAILS = frozenset({
    "business-plan-gate", "sad-model", "post-drill-learning-review", "sad-model-update",
})

DETAIL_FIELD_OWNERS = {
    "business-plan-gate": {
        "bp_gate_classification": "BP Execution Gate",
        "bp_gate_calculated_td_ft_md": "BP Execution Gate",
        "bp_gate_calculated_td_source": "BP Execution Gate",
        "bp_gate_calculated_td_override_reason": "BP Execution Gate",
        "bp_gate_actual_td_ft_md": "BP Execution Gate",
        "bp_gate_calculated_drilling_days": "BP Execution Gate",
        "bp_gate_actual_drilling_days": "BP Execution Gate",
        "bp_gate_logging_program": "BP Execution Gate",
        "bp_gate_interval_from": "BP Execution Gate",
        "bp_gate_interval_to": "BP Execution Gate",
        "bp_gate_swc": "BP Execution Gate",
        "bp_gate_pressure_points": "BP Execution Gate",
        "bp_gate_fluid_samples": "BP Execution Gate",
        "bp_gate_coring_program": "BP Execution Gate",
        "bp_gate_coring_thickness_ft": "BP Execution Gate",
        "bp_gate_coring_formations": "BP Execution Gate",
        "bp_gate_slides_saved": "BP Execution Gate",
    },
    "well-letters": {
        "well_proposal_shared": "Well Proposal",
        "site_preparation_shared": "Site Preparation",
        "approval_to_drill_shared": "Approval To Drill",
    },
    "gheer-inputs": {
        "gheer_geophysical_shared": "GHEER",
        "gheer_geomechanical_shared": "GHEER",
        "gheer_vsp_required": "GHEER",
    },
    "quicklook-logs": {
        "quicklook_pdf": "Quicklook Logs",
        "quicklook_las": "Quicklook Logs",
    },
    "aramco-approved-pics": {
        "aap_petrel_loaded": "Aramco Picks",
        "aap_geoknowledge_loaded": "Aramco Picks",
    },
    "sad-model": {
        "sad_area_km2_p90": "SAD Model",
        "sad_area_km2_p10": "SAD Model",
        "sad_grv_p90": "SAD Model",
        "sad_grv_p10": "SAD Model",
        "sad_surfaces_polygons_loaded": "SAD Model",
        "sad_slides_loaded": "SAD Model",
        "post_drill_piip_gas_p90": "SAD Model",
        "post_drill_piip_gas_mean": "SAD Model",
        "post_drill_piip_gas_p10": "SAD Model",
        "post_drill_piip_has_liquid": "SAD Model",
        "post_drill_piip_liquid_p90": "SAD Model",
        "post_drill_piip_liquid_mean": "SAD Model",
        "post_drill_piip_liquid_p10": "SAD Model",
    },
    "summary-slides": {
        "exec_summary_loaded": "Executive Summary",
        "ured_update_loaded": "Executive Summary",
    },
    "post-drill-learning-review": {
        "post_well_slides_loaded": "Post-Well Outcome & Decision Gate",
    },
    "flowback-results": {
        "flowback_shared_confirmed": "Flowback Results",
    },
    "sad-model-update": {
        "sad_update_area_km2_p90": "SAD Update",
        "sad_update_area_km2_p10": "SAD Update",
        "sad_update_grv_p90": "SAD Update",
        "sad_update_grv_p10": "SAD Update",
        "sad_update_surfaces_polygons_loaded": "SAD Update",
        "sad_update_slides_loaded": "SAD Update",
        "resource_update_gas_p90": "SAD Update",
        "resource_update_gas_mean": "SAD Update",
        "resource_update_gas_p10": "SAD Update",
        "resource_update_has_liquid": "SAD Update",
        "resource_update_liquid_p90": "SAD Update",
        "resource_update_liquid_mean": "SAD Update",
        "resource_update_liquid_p10": "SAD Update",
    },
    "final-summary-slides": {
        "final_exec_summary_done": "SAD Update",
        "final_ured_update_done": "SAD Update",
    },
    "final-log-analysis": {
        "final_petrel": "Final Log Analysis",
        "final_pdf": "Final Log Analysis",
        "final_las": "Final Log Analysis",
    },
    "structural-mtr": {"structural_mtr_shared": "PVAD Structural MTR"},
    "pda-booking": {
        "pda_complete": "PDA",
        "reserves_booking_response": "PDA",
        "reserves_booking_year": "PDA",
    },
}

BOOLEAN_FIELDS = frozenset({
    key for fields in DETAIL_FIELD_OWNERS.values() for key in fields
    if key.endswith(("_loaded", "_saved", "_shared", "_confirmed", "_required", "_complete"))
} | {"post_drill_piip_has_liquid", "resource_update_has_liquid", "final_exec_summary_done",
     "final_ured_update_done", "quicklook_pdf", "quicklook_las",
     "final_petrel", "final_pdf", "final_las"})

NUMERIC_FIELDS = frozenset({
    "bp_gate_calculated_td_ft_md", "bp_gate_actual_td_ft_md",
    "bp_gate_calculated_drilling_days", "bp_gate_actual_drilling_days",
    "bp_gate_swc", "bp_gate_pressure_points", "bp_gate_fluid_samples",
    "bp_gate_coring_thickness_ft", "sad_area_km2_p90", "sad_area_km2_p10",
    "sad_grv_p90", "sad_grv_p10", "post_drill_piip_gas_p90",
    "post_drill_piip_gas_mean", "post_drill_piip_gas_p10",
    "post_drill_piip_liquid_p90", "post_drill_piip_liquid_mean",
    "post_drill_piip_liquid_p10", "sad_update_area_km2_p90",
    "sad_update_area_km2_p10", "sad_update_grv_p90", "sad_update_grv_p10",
    "resource_update_gas_p90", "resource_update_gas_mean", "resource_update_gas_p10",
    "resource_update_liquid_p90", "resource_update_liquid_mean", "resource_update_liquid_p10",
})

SAD_COPY_PAIRS = (
    ("sad_area_km2_p90", "sad_update_area_km2_p90"),
    ("sad_area_km2_p10", "sad_update_area_km2_p10"),
    ("sad_grv_p90", "sad_update_grv_p90"),
    ("sad_grv_p10", "sad_update_grv_p10"),
    ("sad_surfaces_polygons_loaded", "sad_update_surfaces_polygons_loaded"),
    ("sad_slides_loaded", "sad_update_slides_loaded"),
    ("post_drill_piip_gas_p90", "resource_update_gas_p90"),
    ("post_drill_piip_gas_mean", "resource_update_gas_mean"),
    ("post_drill_piip_gas_p10", "resource_update_gas_p10"),
    ("post_drill_piip_has_liquid", "resource_update_has_liquid"),
    ("post_drill_piip_liquid_p90", "resource_update_liquid_p90"),
    ("post_drill_piip_liquid_mean", "resource_update_liquid_mean"),
    ("post_drill_piip_liquid_p10", "resource_update_liquid_p10"),
)


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _number(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _present(value):
    return value is not None and str(value).strip() != ""


def _round_whole(value):
    try:
        return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _field_from_name(name):
    text = str(name or "").strip()
    if "-" in text:
        return text.split("-", 1)[0].strip()
    return text.split()[0] if text else ""


def _json_list(value):
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _task_map(session, project_id, include_inactive=False):
    active_clause = "" if include_inactive else "AND is_active = 1"
    rows = db.fetch_all(session, f"""
        SELECT * FROM project_tasks
        WHERE project_id = :project_id {active_clause}
        ORDER BY is_active, sequence_no
    """, {"project_id": project_id})
    result = {}
    for row in rows:
        result[row["task_name"]] = row
    return result


def _field_maps(session, project_id):
    rows = db.fetch_all(session, """
        SELECT pt.task_name, pt.is_active, f.field_key, f.field_value
        FROM project_tasks pt
        JOIN task_dynamic_fields f ON f.task_id = pt.task_id
        WHERE pt.project_id = :project_id
        ORDER BY pt.is_active, pt.sequence_no
    """, {"project_id": project_id})
    result = {}
    for row in rows:
        result.setdefault(row["task_name"], {})[row["field_key"]] = row["field_value"]
    return result


def _formation_rows(session, project_id, phase=None):
    params = {"project_id": project_id}
    phase_sql = ""
    if phase:
        phase_sql = "AND phase = :phase"
        params["phase"] = phase
    rows = db.fetch_all(session, f"""
        SELECT * FROM project_formations
        WHERE project_id = :project_id {phase_sql}
        ORDER BY id
    """, params)
    intervals = db.fetch_all(session, f"""
        SELECT * FROM project_formation_pay_intervals
        WHERE project_id = :project_id {phase_sql}
        ORDER BY formation, seq, id
    """, params)
    grouped = {}
    for interval in intervals:
        grouped.setdefault((interval["phase"], interval["formation"]), []).append(interval)
    for row in rows:
        row["pay_intervals"] = grouped.get((row["phase"], row["formation"]), [])
    return rows


def _value(fields, task_name, key):
    return (fields.get(task_name) or {}).get(key)


def _flat_values(fields):
    values = {}
    for detail_fields in DETAIL_FIELD_OWNERS.values():
        for key, task_name in detail_fields.items():
            values[key] = _value(fields, task_name, key)
    for slug, detail in DETAILS.items():
        key = "bpe_comments_" + slug.replace("-", "_")
        values[key] = _value(fields, detail["task_name"], key)
    return values


def _fluid_state(formations):
    intervals = [interval for row in formations if row.get("phase") == "quicklook"
                 for interval in row.get("pay_intervals", [])]
    values = [str(interval.get("fluid") or "").strip() for interval in intervals]
    success = any(value in PRODUCTIVE_FLUIDS for value in values)
    if not values or any(value not in FLUIDS for value in values):
        decision = "incomplete"
    elif all(value in NON_PRODUCTIVE_FLUIDS for value in values):
        decision = "all_water_or_dry"
    else:
        decision = "productive"
    return {"decision": decision, "successful": success, "fluids": values}


def _formation_complete(formations, phase):
    rows = [row for row in formations if row.get("phase") == phase]
    if not rows:
        return False
    formation_required = ("top_tvdss_ft", "base_tvdss_ft", "thickness_ft")
    interval_required = ("top_tvdss_ft", "base_tvdss_ft", "phit_pct", "swt_pct", "ngr_pct", "kint_md")
    for row in rows:
        if not str(row.get("formation") or "").strip():
            return False
        if any(_number(row.get(key)) is None for key in formation_required):
            return False
        intervals = row.get("pay_intervals") or []
        if not intervals:
            return False
        for interval in intervals:
            if any(_number(interval.get(key)) is None for key in interval_required):
                return False
            if str(interval.get("fluid") or "").strip() not in FLUIDS:
                return False
    return True


def _sad_complete(values, update=False):
    prefix = "resource_update" if update else "post_drill_piip"
    base = "sad_update" if update else "sad"
    required = (
        f"{base}_area_km2_p90", f"{base}_area_km2_p10",
        f"{base}_grv_p90", f"{base}_grv_p10",
        f"{prefix}_gas_p90", f"{prefix}_gas_mean", f"{prefix}_gas_p10",
    )
    if any(_number(values.get(key)) is None for key in required):
        return False
    if not _truthy(values.get(f"{base}_surfaces_polygons_loaded")):
        return False
    if not _truthy(values.get(f"{base}_slides_loaded")):
        return False
    if _number(values[f"{base}_area_km2_p10"]) < _number(values[f"{base}_area_km2_p90"]):
        return False
    if _number(values[f"{base}_grv_p10"]) < _number(values[f"{base}_grv_p90"]):
        return False
    if not (_number(values[f"{prefix}_gas_p90"]) <= _number(values[f"{prefix}_gas_mean"])
            <= _number(values[f"{prefix}_gas_p10"])):
        return False
    if _truthy(values.get(f"{prefix}_has_liquid")):
        liquid = (f"{prefix}_liquid_p90", f"{prefix}_liquid_mean", f"{prefix}_liquid_p10")
        if any(_number(values.get(key)) is None for key in liquid):
            return False
        if not (_number(values[liquid[0]]) <= _number(values[liquid[1]]) <= _number(values[liquid[2]])):
            return False
    return True


def _flowback_rows(fields):
    return _json_list(_value(fields, "Flowback Results", "flowback_stages_rows"))


def _flowback_complete(values, rows):
    if not rows or not _truthy(values.get("flowback_shared_confirmed")):
        return False
    required = ("formation", "top_md", "base_md", "choke_size_in", "fwhp_psi")
    for row in rows:
        if not str(row.get("formation") or "").strip():
            return False
        if any(_number(row.get(key)) is None for key in required[1:]):
            return False
    return True


def _sad_update_branch(values, fluid_state, flowback_rows):
    if fluid_state["decision"] == "incomplete":
        return "blocked_fluid"
    if fluid_state["decision"] == "all_water_or_dry":
        return "water_or_dry_bypass"
    if len(flowback_rows) != 1:
        return "unresolved_comparison"
    row = flowback_rows[0]
    dynamic_area = _number(row.get("dynamic_area_km2"))
    dynamic_ogip = _number(row.get("dynamic_ogip_bcf"))
    threshold_area = _number(values.get("sad_area_km2_p90"))
    threshold_ogip = _number(values.get("post_drill_piip_gas_mean"))
    if None in (dynamic_area, dynamic_ogip, threshold_area, threshold_ogip):
        return "unresolved_comparison"
    if dynamic_area > threshold_area or dynamic_ogip > threshold_ogip:
        return "manual_update"
    return "copied_from_sad"


def _state(completed=False, pending=False, source="manual", locked=False):
    if pending:
        return {"status": "Pending Approval", "color": "orange", "source": "approval", "locked": False}
    if completed:
        color = "gray" if source == "system" else "green"
        return {"status": "Completed", "color": color, "source": source, "locked": bool(locked)}
    return {"status": "In Progress", "color": "empty", "source": source, "locked": bool(locked)}


def _approval_state(task):
    status = (task or {}).get("status") or "Not Assigned"
    if status == "Approved":
        return _state(completed=True, source="approval")
    if status == "Ready":
        return _state(pending=True)
    return _state()


def _effective_state(project, tasks, fields, formations):
    values = _flat_values(fields)
    fluid = _fluid_state(formations)
    flow_rows = _flowback_rows(fields)
    branch = _sad_update_branch(values, fluid, flow_rows)
    classification = values.get("bp_gate_classification")
    dry_path = fluid["decision"] == "all_water_or_dry"

    states = {
        "business-plan-gate": _approval_state(tasks.get("BP Execution Gate")),
        "well-proposal": _state(
            completed=classification == "Development" or _truthy(values.get("well_proposal_shared")),
            source="system" if classification == "Development" else "manual",
            locked=classification == "Development",
        ),
        "site-preparation": _state(completed=_truthy(values.get("site_preparation_shared"))),
        "approval-to-drill": _state(completed=_truthy(values.get("approval_to_drill_shared"))),
        "gheer-geophysics": _state(completed=_truthy(values.get("gheer_geophysical_shared"))),
        "gheer-geomechanics": _state(completed=_truthy(values.get("gheer_geomechanical_shared"))),
        "quicklook-logs": _state(completed=(
            _formation_complete(formations, "quicklook")
            and _truthy(values.get("quicklook_pdf"))
            and _truthy(values.get("quicklook_las"))
        )),
        "aap": _state(completed=(
            _truthy(values.get("aap_petrel_loaded"))
            and _truthy(values.get("aap_geoknowledge_loaded"))
        )),
        "sad-model": _approval_state(tasks.get("SAD Model")),
        "executive-summary": _state(
            completed=dry_path or _truthy(values.get("exec_summary_loaded")),
            source="system" if dry_path else "manual", locked=dry_path,
        ),
        "ured-update": _state(
            completed=dry_path or _truthy(values.get("ured_update_loaded")),
            source="system" if dry_path else "manual", locked=dry_path,
        ),
        "learnings": _approval_state(tasks.get("Post-Well Outcome & Decision Gate")),
        "flowback": (_state(completed=True, source="system", locked=True) if dry_path
                     else _state(completed=_flowback_complete(values, flow_rows))),
        "sad-update": (_state(completed=True, source="system", locked=True)
                       if branch in {"water_or_dry_bypass", "copied_from_sad"}
                       else _approval_state(tasks.get("SAD Update")) if branch == "manual_update"
                       else _state()),
        "final-logs": _state(completed=(
            _formation_complete(formations, "final")
            and _truthy(values.get("final_petrel"))
            and _truthy(values.get("final_pdf"))
            and _truthy(values.get("final_las"))
        )),
        "mtr": (_state(completed=True, source="system", locked=True) if dry_path
                else _state(completed=_truthy(values.get("structural_mtr_shared")))),
    }

    final_exec = dry_path or _truthy(values.get("final_exec_summary_done"))
    final_ured_system = dry_path or branch == "copied_from_sad"
    final_ured = final_ured_system or _truthy(values.get("final_ured_update_done"))
    states["final-summary"] = _state(
        completed=final_exec and final_ured,
        source="system" if dry_path and final_exec and final_ured else "manual",
        locked=dry_path,
    )

    pda_manual = _truthy(values.get("pda_complete"))
    pda_checked = classification == "Development" or pda_manual
    response = "No" if dry_path else str(values.get("reserves_booking_response") or "")
    booking_year = str(values.get("reserves_booking_year") or "")
    valid_booking = response == "No" or (response == "Yes" and booking_year.isdigit())
    pda_done = pda_checked and response in {"Yes", "No"} and valid_booking
    pda_gray = pda_done and classification == "Development" and response == "No"
    states["pda-booking"] = _state(
        completed=pda_done,
        source="system" if pda_gray else "manual",
        locked=dry_path,
    )

    state_by_stage = {}
    for stage in STAGES:
        items = []
        for key, label, detail_slug in stage["items"]:
            item = dict(states[key])
            item.update({"key": key, "label": label, "detail_slug": detail_slug})
            items.append(item)
        state_by_stage[stage["key"]] = items
    current = STAGES[-1]
    for stage in STAGES:
        if not all(item["status"] == "Completed" for item in state_by_stage[stage["key"]]):
            current = stage
            break
    return {
        "values": values,
        "fluid": fluid,
        "sad_update_branch": branch,
        "states": states,
        "stages": state_by_stage,
        "current_stage": current,
        "flowback_rows": flow_rows,
    }


def _project_context(session, project_id):
    project = db.fetch_one(session, "SELECT * FROM projects WHERE project_id = :project_id",
                           {"project_id": project_id})
    if not project or int(project.get("archived") or 0):
        raise ValueError("Business Plan well not found.")
    if not (str(project.get("pipeline_type") or "").lower() == "bp"
            or int(project.get("business_plan_enabled") or 0) == 1):
        raise ValueError("This record is not in Business Plan Execution.")
    tasks = _task_map(session, project_id)
    fields = _field_maps(session, project_id)
    formations = _formation_rows(session, project_id)
    effective = _effective_state(project, tasks, fields, formations)
    return project, tasks, fields, formations, effective


def _assignees(tasks):
    values = []
    for task_name in TASK_NAMES:
        name = str((tasks.get(task_name) or {}).get("assigned_to") or "").strip()
        if name and name not in values:
            values.append(name)
    return values


def _well_projection(project, tasks, fields, formations, effective):
    current = effective["current_stage"]
    items = effective["stages"][current["key"]]
    completed = sum(1 for item in items if item["status"] == "Completed")
    assignees = _assignees(tasks)
    # Card 3V: a record is known by the name it was STAKED under, once staking
    # is CONFIRMED; the lead name it was matured under travels alongside so the
    # pairing stays recoverable. `field` is still derived from the LEAD name --
    # the field is where the segment is, and a staked name is not guaranteed to
    # carry the same prefix.
    staked_name = _value(fields, "Well Site Location", STAKED_WELL_NAME_FIELD)
    confirmed = staking_confirmed(fields.get("Well Site Location") or {})
    return {
        "project_id": project["project_id"],
        "project_name": display_record_name(project["project_name"], staked_name, confirmed),
        "lead_name": project["project_name"],
        "staked_well_name": staked_name or "",
        "field": _field_from_name(project["project_name"]),
        "business_plan_year": project.get("business_plan_year"),
        "priority": project.get("priority") if project.get("priority") in PRIORITIES else "Low",
        "assignees": assignees,
        "assignee_label": ", ".join(assignees) if assignees else "Not Assigned",
        "stage_key": current["key"],
        "stage_label": current["label"],
        "items": items,
        "completed_count": completed,
        "progress_percent": _round_whole(100 * completed / 6),
        "all_states": effective["states"],
        # What the Pre-Drilling column's "BP Gate" toggle asks: is this well
        # still sitting at the gate? Computed here so the client filters on a
        # stated fact rather than re-deriving it from the item list.
        "at_business_plan_gate":
            effective["states"]["business-plan-gate"]["status"] != "Completed",
        # Card 3X. The animated border is shown only when this is on AND the
        # card sits under Post-Drilling, so the card carries both facts.
        "active_drilling": 1 if _truthy(
            _value(fields, "Quicklook Logs", "active_drilling")
            or _value(fields, "Quicklook Logs Interpretation", "active_drilling")) else 0,
        "actual_drilling_days": _number(effective["values"].get("bp_gate_actual_drilling_days")),
        "gate_approved": (tasks.get("BP Execution Gate") or {}).get("status") == "Approved",
        "successful": effective["fluid"]["successful"],
        "fluid_decision": effective["fluid"]["decision"],
        "sad_update_branch": effective["sad_update_branch"],
    }


def _simulated_mean(fields):
    for task_name in ("Pre-Drilling GeoX Assessment", "Pre-Drilling Resource Assessment"):
        value = _value(fields, task_name, "pre_drill_piip_gas_mean")
        if _present(value):
            return _number(value)
    return None


def _actual_mean(fields):
    for task_name, key in (
        ("SAD Update", "resource_update_gas_mean"),
        ("Resource Assessment Update", "resource_update_gas_mean"),
        ("SAD Model", "post_drill_piip_gas_mean"),
        ("Post-Drilling Resource Assessment", "post_drill_piip_gas_mean"),
    ):
        value = _value(fields, task_name, key)
        if _present(value):
            return _number(value)
    return None


def _matches_filters(well, filters):
    assignee = filters.get("assignee", "All Assignees")
    if assignee == "Unassigned" and well["assignees"]:
        return False
    if assignee not in {"All Assignees", "Unassigned"} and assignee not in well["assignees"]:
        return False
    field = filters.get("field", "All Fields")
    if field != "All Fields" and well["field"] != field:
        return False
    if str(filters.get("year") or "") != ALL_YEARS:
        try:
            if int(well.get("business_plan_year") or 0) != int(filters["year"]):
                return False
        except (TypeError, ValueError):
            return False
    # "all" is the default, not "business-plan-gate": this is the STEP filter,
    # and defaulting it to the gate quietly restricted every caller to
    # Pre-Drilling wells. Narrowing to the gate is the Pre-Drilling column's own
    # toggle (static/js/views/business-plan.js), which reaches one column.
    step = filters.get("step", "all")
    status = filters.get("status", "All Status")
    current_keys = {item["key"] for item in well["items"]}
    if step != "all" and step not in current_keys:
        return False
    if status != "All Status":
        candidates = well["items"] if step == "all" else [well["all_states"][step]]
        if not any(item["status"] == status for item in candidates):
            return False
    return True


def get_dashboard(session, filters=None):
    filters = dict(filters or {})
    filters.setdefault("assignee", "All Assignees")
    filters.setdefault("field", "All Fields")
    filters.setdefault("status", "All Status")
    filters.setdefault("year", date.today().year)
    filters.setdefault("step", "all")

    projects = db.fetch_all(session, """
        SELECT * FROM projects
        WHERE archived = 0 AND (pipeline_type = 'bp' OR business_plan_enabled = 1)
        ORDER BY project_name, project_id
    """)
    all_wells = []
    fields_set = set()
    assignees_set = set()
    out_of_range_years = set()
    context_by_id = {}
    for project in projects:
        tasks = _task_map(session, project["project_id"])
        field_map = _field_maps(session, project["project_id"])
        formations = _formation_rows(session, project["project_id"])
        effective = _effective_state(project, tasks, field_map, formations)
        well = _well_projection(project, tasks, field_map, formations, effective)
        all_wells.append(well)
        context_by_id[project["project_id"]] = (field_map, well)
        if well["field"]:
            fields_set.add(well["field"])
        assignees_set.update(well["assignees"])
        year = project.get("business_plan_year")
        if year is not None and not config.BPE_YEAR_MIN <= int(year) <= config.BPE_YEAR_MAX:
            out_of_range_years.add(int(year))

    visible = [well for well in all_wells if _matches_filters(well, filters)]
    priority_rank = {"High": 0, "Medium": 1, "Low": 2}
    visible.sort(key=lambda row: (priority_rank.get(row["priority"], 2), row["project_name"].lower(), row["project_id"]))

    rig_target = sum(well["actual_drilling_days"] or 0 for well in visible)
    rig_inventory = sum((well["actual_drilling_days"] or 0) for well in visible if well["gate_approved"])
    successful = sum(1 for well in visible if well["successful"])
    simulated_total = 0.0
    actual_total = 0.0
    missing_simulated = []
    inconsistent_actual = []
    for well in visible:
        field_map, _projection = context_by_id[well["project_id"]]
        simulated = _simulated_mean(field_map)
        if simulated is None:
            missing_simulated.append(well["project_id"])
        else:
            simulated_total += simulated
        actual = _actual_mean(field_map)
        if well["successful"] and actual is not None:
            actual_total += actual
        elif not well["successful"] and actual not in (None, 0.0):
            inconsistent_actual.append(well["project_id"])

    stage_counts = {stage["key"]: 0 for stage in STAGES}
    for well in visible:
        stage_counts[well["stage_key"]] += 1
    return {
        "filters": filters,
        "options": {
            "assignees": ["All Assignees", "Unassigned"] + sorted(assignees_set, key=str.lower),
            "fields": ["All Fields"] + sorted(fields_set, key=str.lower),
            "statuses": ["All Status", "Completed", "Pending Approval", "In Progress"],
            "years": list(range(config.BPE_YEAR_MIN, config.BPE_YEAR_MAX + 1)),
            "steps": [{"value": "all", "label": "All Steps"}] + list(STEP_OPTIONS),
        },
        "scope": "current-stage tracking items",
        "out_of_range_years": sorted(out_of_range_years),
        "stage_counts": stage_counts,
        "kpis": {
            "rig_inventory_days": rig_inventory,
            "rig_target_days": rig_target,
            "success_rate_pct": _round_whole(100 * successful / len(visible)) if visible else 0,
            "actual_mean_ogip_bcf": _round_whole(actual_total),
            "simulated_mean_ogip_bcf": _round_whole(simulated_total),
        },
        "data_quality": {
            "missing_simulated_mean_project_ids": missing_simulated,
            "unsuccessful_with_actual_project_ids": inconsistent_actual,
        },
        "wells": visible,
    }


def _detail_status(items):
    """Roll one navigation entry's tracking items up into a single status.

    A navigation DETAIL can own several tracking items -- Well Letters owns
    three, Summary Slides owns two -- so its status is the same roll-up the
    boards apply to a record: everything approved reads Completed, anything
    waiting on a supervisor reads Pending Approval, and the rest reads In
    Progress.  An entry with no items (there are none today) reads In Progress
    rather than inheriting all()'s vacuous truth.
    """
    if items and all(item["status"] == "Completed" for item in items):
        return "Completed"
    if any(item["status"] == "Pending Approval" for item in items):
        return "Pending Approval"
    return "In Progress"


def _navigation(effective):
    """The detail page's step rail: every step of every stage, with its status.

    The status travels WITH the entry because the rail tints each step by it;
    deriving it in the client would mean a second copy of the roll-up rule
    above, and the client has no per-step state for the stages it is not on.
    """
    groups = []
    for stage in STAGES:
        by_detail = {}
        for item in effective["stages"][stage["key"]]:
            by_detail.setdefault(item["detail_slug"], []).append(item)
        groups.append({
            "stage_key": stage["key"],
            "stage_label": stage["label"],
            "details": [
                {"slug": slug, "label": label, "status": _detail_status(by_detail.get(slug, []))}
                for slug, label, _task in stage["details"]
            ],
        })
    return groups


def get_detail(session, project_id, detail_slug):
    if detail_slug not in DETAILS:
        raise ValueError("Unknown Business Plan detail step.")
    project, tasks, fields, formations, effective = _project_context(session, project_id)
    detail = DETAILS[detail_slug]
    task = tasks.get(detail["task_name"])
    if not task:
        raise ValueError("Business Plan component not found.")
    state_keys = [key for key, _label, slug in next(
        stage for stage in STAGES if stage["key"] == detail["stage_key"]
    )["items"] if slug == detail_slug]
    expected_phase = (
        "quicklook" if detail_slug == "quicklook-logs" else
        "final" if detail_slug == "final-log-analysis" else None
    )
    custom_formations = sorted({
        row["formation"] for row in formations if row.get("formation") not in FORMATIONS
    })
    return {
        "project": {
            "project_id": project["project_id"],
            # Same rule as the board: the staked well name once staking is
            # confirmed, with the lead name carried alongside.
            "project_name": display_record_name(
                project["project_name"],
                _value(fields, "Well Site Location", STAKED_WELL_NAME_FIELD),
                staking_confirmed(fields.get("Well Site Location") or {})),
            "lead_name": project["project_name"],
            "staked_well_name": _value(fields, "Well Site Location", STAKED_WELL_NAME_FIELD) or "",
            "field": _field_from_name(project["project_name"]),
            "business_plan_year": project.get("business_plan_year"),
            "priority": project.get("priority") if project.get("priority") in PRIORITIES else "Low",
        },
        "detail": detail,
        "task": task,
        "assignee": task.get("assigned_to") or "",
        "values": effective["values"],
        "comments_key": "bpe_comments_" + detail_slug.replace("-", "_"),
        "formations": [row for row in formations if row.get("phase") == expected_phase],
        # Card 3E: the Well Summary beside the step is the SAME card the
        # maturation shell renders (static/js/views/detail.js
        # wellSummaryBodyHtml), so it needs the record-level inputs that card
        # reads -- the retired-inclusive field map, every formation phase (the
        # `formations` list above is deliberately filtered to this step's own
        # phase), the frozen lead snapshot and the read-time Total CoS. They
        # ride on THIS payload rather than a second request so the panel can
        # never show a different vintage from the step beside it.
        "well_summary": {
            "fields": fields,
            "formations": formations,
            "lead_summary": get_lead_summary_snapshot(session, project_id),
            "derisking": get_project_overview(session, project_id).get("derisking", ""),
        },
        "flowback_stages": effective["flowback_rows"],
        "flowback_initialized": "flowback_stages_rows" in (fields.get("Flowback Results") or {}),
        "fluid_state": effective["fluid"],
        "sad_update_branch": effective["sad_update_branch"],
        "tracking": [dict(effective["states"][key], key=key) for key in state_keys],
        "stage_items": effective["stages"][detail["stage_key"]],
        "navigation": _navigation(effective),
        "links": {
            "vsp": config.business_plan_vsp_url(),
            "structural_mtr": config.business_plan_structural_mtr_url(),
        },
        # Both lists are user-maintained in config/lists.yaml (read per
        # request, so an edit needs a restart at most, never a redeploy).
        "hole_sections": list(config.hole_sections()),
        "formation_options": list(config.formations()) + custom_formations,
        "booking_years": list(range(date.today().year, date.today().year + 4)),
    }


def _set_field(session, task, key, value, actor, role, source="user", reason=None,
               correlation=None, force_create=False):
    value = "" if value is None else str(value)
    existing = db.fetch_one(session, """
        SELECT field_value FROM task_dynamic_fields
        WHERE task_id = :task_id AND field_key = :field_key
    """, {"task_id": task["task_id"], "field_key": key})
    old = "" if not existing or existing.get("field_value") is None else str(existing["field_value"])
    if old == value and (existing or not force_create):
        return False
    now = utc_now_str()
    db.execute(session, """
        INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
        VALUES (:task_id, :field_key, :field_value, :now)
        ON CONFLICT(task_id, field_key) DO UPDATE SET
          field_value = excluded.field_value, updated_at = excluded.updated_at
    """, {"task_id": task["task_id"], "field_key": key, "field_value": value, "now": now})
    context = {
        "field": key, "old": old, "new": value, "role": role or "unknown",
        "source": source, "reason": reason or "", "correlation": correlation or "",
    }
    log_task_event(
        session, task["task_id"], task["project_id"], task["task_name"],
        "Business Plan Field Updated", old, value, actor,
        json.dumps(context, sort_keys=True, separators=(",", ":")),
    )
    db.execute(session, """
        UPDATE project_tasks SET last_updated = :now, revision = revision + 1
        WHERE task_id = :task_id
    """, {"now": now, "task_id": task["task_id"]})
    db.execute(session, """
        UPDATE projects SET last_updated = :now, revision = revision + 1
        WHERE project_id = :project_id
    """, {"now": now, "project_id": task["project_id"]})
    return True


def _normalized_value(key, value):
    if key in BOOLEAN_FIELDS:
        return "1" if _truthy(value) else "0"
    if key in NUMERIC_FIELDS:
        if value is None or str(value).strip() == "":
            return ""
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation:
            raise ValueError("Enter a valid number.")
        return format(number, "f").rstrip("0").rstrip(".") if "." in format(number, "f") else format(number, "f")
    if key == "bp_gate_classification" and value not in CLASSIFICATIONS:
        raise ValueError("Select Development, Appraisal, or Exploration.")
    if key == "bp_gate_logging_program" and value not in LOGGING_PROGRAMS:
        raise ValueError("Select a valid Logging Program.")
    if key == "bp_gate_coring_program" and value not in {"Yes", "No"}:
        raise ValueError("Select Yes or No for Coring Program.")
    if key == "reserves_booking_response" and value not in {"", "Yes", "No"}:
        raise ValueError("Select Yes or No for Reserves Booking.")
    if key == "bp_gate_coring_formations":
        values = value if isinstance(value, list) else _json_list(value)
        return json.dumps([str(item).strip() for item in values if str(item).strip()], separators=(",", ":"))
    return "" if value is None else str(value).strip()


def _classification_defaults(classification):
    if classification == "Development":
        return {
            "bp_gate_logging_program": "Optimized Standard B",
            "bp_gate_swc": "0", "bp_gate_pressure_points": "3",
            "bp_gate_fluid_samples": "3", "bp_gate_coring_program": "No",
        }
    return {
        "bp_gate_logging_program": "Standard A",
        "bp_gate_swc": "30", "bp_gate_pressure_points": "20",
        "bp_gate_fluid_samples": "5", "bp_gate_coring_program": "No",
    }


def _approved_source_edit_blocked(detail_slug, key, tasks):
    if key.startswith("bpe_comments_"):
        return False
    task = tasks.get(DETAILS[detail_slug]["task_name"])
    return (detail_slug in APPROVAL_DETAILS and task
            and task.get("status") in {"Ready", "Approved"})


def _sad_branch_change_error(tasks, before, after):
    sad_update = tasks.get("SAD Update") or {}
    if before["sad_update_branch"] == after["sad_update_branch"]:
        return None
    if sad_update.get("status") == "Ready":
        return "Return the pending SAD Model Update before changing source data that changes its branch."
    if sad_update.get("status") == "Approved":
        return "Reopen the approved SAD Model Update before changing source data that changes its branch."
    return None


def save_field(session, project_id, detail_slug, key, value, actor="Web User", role="employee",
               confirm_reset=False, override_reason=None):
    if detail_slug not in DETAILS:
        raise ValueError("Unknown Business Plan detail step.")
    project, tasks, fields, _formations, effective = _project_context(session, project_id)
    detail = DETAILS[detail_slug]
    comments_key = "bpe_comments_" + detail_slug.replace("-", "_")
    owners = DETAIL_FIELD_OWNERS.get(detail_slug, {})
    owner_name = detail["task_name"] if key == comments_key else owners.get(key)
    if not owner_name:
        raise ValueError("This field does not belong to the selected Business Plan step.")
    task = tasks.get(owner_name)
    if not task:
        raise ValueError("Business Plan component not found.")
    if _approved_source_edit_blocked(detail_slug, key, tasks):
        status = (tasks.get(detail["task_name"]) or {}).get("status")
        if status == "Ready":
            raise ValueError("This pending step must be returned before its source fields can change.")
        raise ValueError("This approved step must be explicitly reopened before its source fields can change.")
    if detail_slug == "sad-model-update" and effective["sad_update_branch"] != "manual_update" and key != comments_key:
        raise ValueError("SAD Model Update is locked until the approved comparison selects the manual branch.")
    if key == "well_proposal_shared" and effective["values"].get("bp_gate_classification") == "Development":
        raise ValueError("Well Proposal is controlled by the Development classification rule.")
    if key in {"exec_summary_loaded", "ured_update_loaded", "structural_mtr_shared"} \
            and effective["fluid"]["decision"] == "all_water_or_dry":
        raise ValueError("This field is locked by the Water Bearing/Dry Hole rule.")
    if key in {"reserves_booking_response", "reserves_booking_year"} \
            and effective["fluid"]["decision"] == "all_water_or_dry":
        raise ValueError("Reserves Booking is locked by the Water Bearing/Dry Hole rule.")
    if key == "pda_complete" and effective["values"].get("bp_gate_classification") == "Development":
        raise ValueError("Post-Drilling Analysis is controlled by the Development classification rule.")
    if key == "bp_gate_calculated_drilling_days":
        raise ValueError("Calculated Drilling Days is awaiting the approved calculation configuration.")
    if key == "bp_gate_calculated_td_ft_md":
        if role != "supervisor":
            raise PermissionError("Only a Supervisor may enter a Calculated TD override.")
        reason = str(override_reason or "").strip()
        if not reason:
            raise ValueError("A reason is required for the Calculated TD override.")
    normalized = _normalized_value(key, value)
    correlation = str(uuid.uuid4())
    old_classification = effective["values"].get("bp_gate_classification")
    if key == "bp_gate_classification" and old_classification and old_classification != normalized and not confirm_reset:
        raise ValueError("Confirm the classification change to reset classification-driven defaults.")

    with db.write_transaction(session):
        if key == "bp_gate_classification" and old_classification != normalized:
            _set_field(session, task, key, normalized, actor, role, "user",
                       "classification reset confirmed" if old_classification else "initial classification",
                       correlation)
            for default_key, default_value in _classification_defaults(normalized).items():
                _set_field(session, task, default_key, default_value, actor, role, "system",
                           "classification defaults", correlation)
        else:
            _set_field(session, task, key, normalized, actor, role, "user", correlation=correlation)
            if key == "bp_gate_logging_program" and normalized == "Standard B":
                for default_key, default_value in {
                    "bp_gate_swc": "0", "bp_gate_pressure_points": "3", "bp_gate_fluid_samples": "3",
                }.items():
                    _set_field(session, task, default_key, default_value, actor, role, "system",
                               "Standard B defaults", correlation)
            if key == "bp_gate_calculated_td_ft_md":
                _set_field(session, task, "bp_gate_calculated_td_source", "Supervisor override",
                           actor, role, "supervisor", override_reason, correlation)
                _set_field(session, task, "bp_gate_calculated_td_override_reason", override_reason,
                           actor, role, "supervisor", override_reason, correlation)
                current_actual = _value(fields, "BP Execution Gate", "bp_gate_actual_td_ft_md")
                if not _present(current_actual):
                    _set_field(session, task, "bp_gate_actual_td_ft_md", normalized, actor, role,
                               "system", "initialized from Calculated Business Plan TD", correlation)
        preview = _effective_state(
            project, tasks, _field_maps(session, project_id), _formation_rows(session, project_id))
        branch_error = _sad_branch_change_error(tasks, effective, preview)
        if branch_error:
            raise ValueError(branch_error)
        _reconcile_system_state(session, project_id, actor, role, correlation)
        new_tasks = _task_map(session, project_id)
        new_effective = _effective_state(
            project, new_tasks, _field_maps(session, project_id), _formation_rows(session, project_id))
        _audit_effective_changes(
            session, new_tasks, effective, new_effective, actor, role, correlation,
            f"field={key}",
        )
    return get_detail(session, project_id, detail_slug)


def _gate_errors(values):
    errors = []
    required = {
        "bp_gate_classification": "Well Classification",
        "bp_gate_calculated_td_ft_md": "Calculated Business Plan TD",
        "bp_gate_actual_td_ft_md": "Actual Business Plan TD",
        # Calculated Drilling Days is NOT required: the field is locked (no
        # equation ships yet), so requiring it would make the Gate
        # unapprovable. It is still validated as numeric when present.
        "bp_gate_actual_drilling_days": "Actual Drilling Days",
        "bp_gate_logging_program": "Logging Program",
        "bp_gate_interval_from": "Interval From",
        "bp_gate_interval_to": "Interval To",
        "bp_gate_swc": "SWC",
        "bp_gate_pressure_points": "Pressure Points",
        "bp_gate_fluid_samples": "Fluid Samples",
        "bp_gate_coring_program": "Coring Program",
    }
    for key, label in required.items():
        if not _present(values.get(key)):
            errors.append(label + " is required.")
    for key in ("bp_gate_calculated_td_ft_md", "bp_gate_actual_td_ft_md",
                "bp_gate_calculated_drilling_days", "bp_gate_actual_drilling_days",
                "bp_gate_swc", "bp_gate_pressure_points", "bp_gate_fluid_samples"):
        if _present(values.get(key)) and _number(values.get(key)) is None:
            errors.append(key.replace("bp_gate_", "").replace("_", " ").title() + " must be numeric.")
    program = values.get("bp_gate_logging_program")
    if (program in {"Standard A", "Standard B"}
            and _present(values.get("bp_gate_interval_from"))
            and values.get("bp_gate_interval_from") == values.get("bp_gate_interval_to")):
        errors.append("Interval From and Interval To cannot be the same under Standard A or Standard B.")
    if values.get("bp_gate_coring_program") == "Yes":
        if _number(values.get("bp_gate_coring_thickness_ft")) is None:
            errors.append("Coring Thickness is required when Coring Program is Yes.")
        if not _json_list(values.get("bp_gate_coring_formations")):
            errors.append("Select at least one Coring Formation.")
    if not _truthy(values.get("bp_gate_slides_saved")):
        errors.append("Business Plan Execution Gate slides must be saved in the shared folder.")
    return errors


def _sad_errors(values, update=False):
    if _sad_complete(values, update=update):
        return []
    return ["Complete all required SAD values and both shared-folder confirmations."]


def _approval_errors(detail_slug, effective):
    values = effective["values"]
    if detail_slug == "business-plan-gate":
        return _gate_errors(values)
    if detail_slug == "sad-model":
        if effective["fluid"]["decision"] == "incomplete":
            return ["Complete the Quicklook Pay Interval Fluid selections before submitting SAD Model."]
        return _sad_errors(values)
    if detail_slug == "post-drill-learning-review":
        return [] if _truthy(values.get("post_well_slides_loaded")) else [
            "Post-Drill Learning Review slides must be placed in the shared folder."]
    if detail_slug == "sad-model-update":
        if effective["sad_update_branch"] != "manual_update":
            return ["SAD Model Update approval is available only in the manual comparison branch."]
        return _sad_errors(values, update=True)
    return ["This step does not use approval."]


def transition_approval(session, project_id, detail_slug, action, actor="Web User", role="employee", comment=""):
    if detail_slug not in APPROVAL_DETAILS:
        raise ValueError("This Business Plan step does not use approval.")
    if action not in {"submit", "approve", "return", "reopen"}:
        raise ValueError("Unknown approval action.")
    if action in {"approve", "return", "reopen"} and role != "supervisor":
        raise PermissionError("Only a Supervisor may perform this approval action.")
    project, tasks, _fields, _formations, effective = _project_context(session, project_id)
    task = tasks.get(DETAILS[detail_slug]["task_name"])
    if not task:
        raise ValueError("Business Plan component not found.")
    current = task.get("status") or "Not Assigned"
    if action in {"submit", "approve"}:
        errors = _approval_errors(detail_slug, effective)
        if errors:
            raise ValueError(" ".join(errors))
    expected = {
        "submit": {"Not Assigned", "In Progress"},
        "approve": {"Ready"},
        "return": {"Ready"},
        "reopen": {"Approved"},
    }[action]
    if current not in expected:
        raise ValueError(f"Cannot {action} this step while it is {current}.")
    new_status = {"submit": "Ready", "approve": "Approved", "return": "In Progress", "reopen": "In Progress"}[action]
    now = utc_now_str()
    correlation = str(uuid.uuid4())
    actual_start = task.get("actual_start") or today_str()
    actual_finish = today_str() if new_status == "Approved" else None
    with db.write_transaction(session):
        db.execute(session, """
            UPDATE project_tasks
            SET status = :status, actual_start = :actual_start, actual_finish = :actual_finish,
                last_updated = :now, revision = revision + 1
            WHERE task_id = :task_id
        """, {"status": new_status, "actual_start": actual_start, "actual_finish": actual_finish,
              "now": now, "task_id": task["task_id"]})
        log_task_event(
            session, task["task_id"], project_id, task["task_name"],
            {"submit": "Component Submitted", "approve": "Component Approved",
             "return": "Component Returned", "reopen": "Component Reopened"}[action],
            current, new_status, actor,
            json.dumps({"role": role, "source": "supervisor" if role == "supervisor" else "user",
                        "comment": str(comment or ""), "correlation": correlation},
                       sort_keys=True, separators=(",", ":")),
        )
        # The header bell rides THIS transaction, beside the audit event it
        # mirrors, exactly as lifecycle.transition_task does -- the fan-out
        # policy is shared even though the two state machines are not. The
        # PRE-transition row is what notify_transition wants: it reads
        # assigned_to and the identifiers, none of which this UPDATE changed.
        notify_transition(session, task, action, actor)
        # Approve may have completed the applicable set; return/reopen reopens
        # it. A BPE component is an ordinary project_tasks row, so the same
        # derived completion stamp applies.
        _sync_completed_at(session, project_id)
        db.execute(session, """
            UPDATE projects SET last_updated = :now, revision = revision + 1
            WHERE project_id = :project_id
        """, {"now": now, "project_id": project_id})
        new_tasks = _task_map(session, project_id)
        new_effective = _effective_state(
            project, new_tasks, _field_maps(session, project_id), _formation_rows(session, project_id))
        _audit_effective_changes(
            session, new_tasks, effective, new_effective, actor, role, correlation,
            f"approval={action}",
        )
    return get_detail(session, project_id, detail_slug)


def assign_detail(session, project_id, detail_slug, assignee, actor="Web User", role="staff"):
    if role not in {"supervisor", "staff"}:
        raise PermissionError("Forbidden: requires supervisor or staff role.")
    if detail_slug not in DETAILS:
        raise ValueError("Unknown Business Plan detail step.")
    assignee = str(assignee or "").strip()
    if assignee:
        user = db.fetch_one(session, """
            SELECT name FROM users WHERE is_active = 1 AND lower(name) = lower(:name)
        """, {"name": assignee})
        if not user:
            raise ValueError("Assignee must be an active user.")
        assignee = user["name"]
    project, tasks, _fields, _formations, _effective = _project_context(session, project_id)
    task_names = set(DETAIL_FIELD_OWNERS.get(detail_slug, {}).values()) or {DETAILS[detail_slug]["task_name"]}
    now = utc_now_str()
    with db.write_transaction(session):
        for task_name in task_names:
            task = tasks.get(task_name)
            if not task or str(task.get("assigned_to") or "") == assignee:
                continue
            old = task.get("assigned_to") or "Not Assigned"
            new_status = "In Progress" if assignee and task.get("status") == "Not Assigned" else task.get("status")
            db.execute(session, """
                UPDATE project_tasks
                SET assigned_to = :assignee, status = :status, actual_start = :actual_start,
                    last_updated = :now, revision = revision + 1
                WHERE task_id = :task_id
            """, {"assignee": assignee or None, "status": new_status,
                  "actual_start": task.get("actual_start") or (today_str() if assignee else None),
                  "now": now, "task_id": task["task_id"]})
            log_task_event(session, task["task_id"], project_id, task_name, "Assignment Changed",
                           old, assignee or "Not Assigned", actor,
                           json.dumps({"role": role, "source": "user"}, separators=(",", ":")))
        db.execute(session, "UPDATE projects SET last_updated = :now WHERE project_id = :project_id",
                   {"now": now, "project_id": project_id})
    return get_detail(session, project_id, detail_slug)


def _audit_structure(session, task, action, old, new, actor, role, source="user", reason="",
                     correlation="", context=None):
    payload = {"role": role, "source": source, "reason": reason}
    if correlation:
        payload["correlation"] = correlation
    if context:
        payload.update(context)
    log_task_event(
        session, task["task_id"], task["project_id"], task["task_name"], action,
        old, new, actor,
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )


def _state_audit_value(state):
    return json.dumps({
        "status": state.get("status"),
        "source": state.get("source"),
        "locked": bool(state.get("locked")),
    }, sort_keys=True, separators=(",", ":"))


def _audit_effective_changes(session, tasks, before, after, actor, role, correlation, reason):
    """Append tracking/progress events only where the effective state changed."""
    for stage in STAGES:
        stage_key = stage["key"]
        old_items = {item["key"]: item for item in before["stages"][stage_key]}
        new_items = {item["key"]: item for item in after["stages"][stage_key]}
        for item_key, new_state in new_items.items():
            old_state = old_items[item_key]
            old_value = _state_audit_value(old_state)
            new_value = _state_audit_value(new_state)
            if old_value == new_value:
                continue
            task = tasks.get(ITEM_TASK_NAMES[item_key])
            if not task:
                continue
            source = "system" if "system" in {old_state.get("source"), new_state.get("source")} else "user"
            _audit_structure(
                session, task, "Business Plan Tracking Updated", old_value, new_value,
                actor, role, source=source, reason=reason, correlation=correlation,
                context={"tracking_item": item_key, "stage": ITEM_STAGE_KEYS[item_key]},
            )

        old_completed = sum(item["status"] == "Completed" for item in old_items.values())
        new_completed = sum(item["status"] == "Completed" for item in new_items.values())
        if old_completed == new_completed:
            continue
        anchor_task = tasks.get(stage["details"][0][2])
        if anchor_task:
            _audit_structure(
                session, anchor_task, "Business Plan Progress Updated",
                f"{old_completed}/6", f"{new_completed}/6", actor, role,
                source="system", reason=reason, correlation=correlation,
                context={"stage": stage_key},
            )


def _audit_text(value):
    return "" if value is None else str(value)


def _clean_formation_payload(rows):
    if not isinstance(rows, list):
        raise ValueError("rows must be a list of formation objects.")
    cleaned = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each formation must be an object.")
        formation = str(row.get("formation") or "").strip().upper()
        if not formation or len(formation) > 40:
            raise ValueError("Formation name must be 1 to 40 characters.")
        if formation in seen:
            raise ValueError(f"Duplicate formation '{formation}'.")
        seen.add(formation)
        result = {"id": row.get("id"), "formation": formation}
        for key in ("top_tvdss_ft", "base_tvdss_ft", "thickness_ft", "porosity_pct", "swt_pct", "pay_ft", "ngr_pct"):
            raw = row.get(key)
            if raw is None or str(raw).strip() == "":
                result[key] = None
            else:
                try:
                    result[key] = float(raw)
                except (TypeError, ValueError):
                    raise ValueError(f"Invalid numeric value for {key}.")
        intervals = row.get("pay_intervals") or []
        if not isinstance(intervals, list):
            raise ValueError("pay_intervals must be a list.")
        result["pay_intervals"] = []
        for interval in intervals:
            if not isinstance(interval, dict):
                raise ValueError("Each Pay Interval must be an object.")
            fluid = str(interval.get("fluid") or "").strip()
            if fluid and fluid not in FLUIDS and fluid not in LEGACY_FLUIDS:
                raise ValueError("Select a valid Pay Interval Fluid.")
            item = {"id": interval.get("id"), "fluid": fluid}
            for key in ("top_tvdss_ft", "base_tvdss_ft", "phit_pct", "swt_pct", "ngr_pct", "kint_md"):
                raw = interval.get(key)
                if raw is None or str(raw).strip() == "":
                    item[key] = None
                else:
                    try:
                        item[key] = float(raw)
                    except (TypeError, ValueError):
                        raise ValueError(f"Invalid Pay Interval value for {key}.")
            result["pay_intervals"].append(item)
        cleaned.append(result)
    return cleaned


def save_formations(session, project_id, detail_slug, rows, actor="Web User", role="employee"):
    phase_by_detail = {"quicklook-logs": "quicklook", "final-log-analysis": "final"}
    phase = phase_by_detail.get(detail_slug)
    if not phase:
        raise ValueError("This Business Plan step does not contain Formation data.")
    cleaned = _clean_formation_payload(rows)
    project, tasks, _fields, _formations, old_effective = _project_context(session, project_id)
    task = tasks.get(DETAILS[detail_slug]["task_name"])
    if not task:
        raise ValueError("Business Plan component not found.")
    now = utc_now_str()
    correlation = str(uuid.uuid4())
    with db.write_transaction(session):
        # The stored rows are read INSIDE the write transaction, not before it:
        # every check below decides what the UPDATEs are allowed to do, so
        # validation and writes have to see ONE snapshot. Read before the write
        # lock and a concurrent delete turns a validated UPDATE into a silent
        # zero-row no-op while the audit trail still records the change.
        existing_rows = {row["id"]: row for row in _formation_rows(session, project_id, phase)}
        supplied_ids = {int(row["id"]) for row in cleaned if row.get("id") not in (None, "")}
        if not supplied_ids.issubset(existing_rows):
            raise ValueError("A Formation row is stale or belongs to another well.")
        # Formation names are unique per (project, phase) -- models.py:207 --
        # and kept rows are renamed IN PLACE, so a target name is free only if
        # no SURVIVING stored row still holds it. Rows the payload dropped are
        # deleted below before any rename, so their names ARE free; a name held
        # by a row that stays is not, even when the payload also renames that
        # row away from it. An A<->B swap (or an A->B, B->C chain) therefore
        # needs two saves: SQLite checks the constraint per statement, so no
        # ordering of in-place UPDATEs can carry the swap out in one write.
        surviving_names = {row_id: existing_rows[row_id]["formation"] for row_id in supplied_ids}
        for row in cleaned:
            row_id = int(row["id"]) if row.get("id") not in (None, "") else None
            if any(name == row["formation"] and other_id != row_id
                   for other_id, name in surviving_names.items()):
                raise ValueError(
                    f"Formation '{row['formation']}' already exists in this step. "
                    "Rename or remove the other row in a separate save first.")
        # A pre-v10 fluid label survives a save only as a REPLACE of itself: the
        # interval must already exist and already hold that exact value. Anything
        # else -- a new interval, or an edit that switches one interval to a
        # retired label -- is a write of retired vocabulary and is rejected. This
        # lives here, not in _clean_formation_payload, because it needs the stored
        # rows; the payload cleaner stays a pure function of its argument.
        stored_interval_fluids = {
            int(item["id"]): str(item.get("fluid") or "")
            for row in existing_rows.values() for item in row.get("pay_intervals", [])
        }
        for row in cleaned:
            for interval in row["pay_intervals"]:
                if interval["fluid"] not in LEGACY_FLUIDS:
                    continue
                interval_id = int(interval["id"]) if interval.get("id") not in (None, "") else None
                if interval_id is None or stored_interval_fluids.get(interval_id) != interval["fluid"]:
                    raise ValueError("Select a valid Pay Interval Fluid.")
        # Dropped formations go FIRST, for the same reason the dropped pay
        # intervals do below: the name they occupy is unique per (project,
        # phase), so a kept row can only be renamed onto it once it is gone.
        for removed_id in set(existing_rows) - supplied_ids:
            old = existing_rows[removed_id]
            _audit_structure(session, task, "Formation Removed", str(removed_id), None, actor, role,
                             reason=old["formation"], correlation=correlation)
            db.execute(session, """
                DELETE FROM project_formation_pay_intervals
                WHERE project_id = :project_id AND phase = :phase AND formation = :formation
            """, {"project_id": project_id, "phase": phase, "formation": old["formation"]})
            db.execute(session, "DELETE FROM project_formations WHERE id = :id", {"id": removed_id})
        for row in cleaned:
            row_id = int(row["id"]) if row.get("id") not in (None, "") else None
            old_row = existing_rows.get(row_id)
            params = {key: value for key, value in row.items() if key != "pay_intervals"}
            params.update({"project_id": project_id, "phase": phase, "task_id": task["task_id"],
                           "now": now, "actor": actor})
            if row_id:
                old_name = old_row["formation"]
                for field_key in (
                    "formation", "top_tvdss_ft", "base_tvdss_ft", "thickness_ft",
                    "porosity_pct", "swt_pct", "pay_ft", "ngr_pct",
                ):
                    old_value = _audit_text(old_row.get(field_key))
                    new_value = _audit_text(row.get(field_key))
                    if old_value != new_value:
                        _audit_structure(
                            session, task, "Formation Field Updated", old_value, new_value,
                            actor, role, reason=f"id={row_id};field={field_key}",
                            correlation=correlation,
                        )
                db.execute(session, """
                    UPDATE project_formations SET formation = :formation,
                      top_tvdss_ft = :top_tvdss_ft, base_tvdss_ft = :base_tvdss_ft,
                      thickness_ft = :thickness_ft, porosity_pct = :porosity_pct,
                      swt_pct = :swt_pct, pay_ft = :pay_ft, ngr_pct = :ngr_pct,
                      source_task_id = :task_id, updated_at = :now, updated_by = :actor
                    WHERE id = :id AND project_id = :project_id AND phase = :phase
                """, params)
                if old_name != row["formation"]:
                    db.execute(session, """
                        UPDATE project_formation_pay_intervals SET formation = :formation
                        WHERE project_id = :project_id AND phase = :phase AND formation = :old_name
                    """, {"formation": row["formation"], "project_id": project_id,
                          "phase": phase, "old_name": old_name})
            else:
                result = db.execute(session, """
                    INSERT INTO project_formations (
                      project_id, formation, phase, top_tvdss_ft, base_tvdss_ft, thickness_ft,
                      porosity_pct, swt_pct, pay_ft, ngr_pct, fluid, source_task_id, updated_at, updated_by
                    ) VALUES (
                      :project_id, :formation, :phase, :top_tvdss_ft, :base_tvdss_ft, :thickness_ft,
                      :porosity_pct, :swt_pct, :pay_ft, :ngr_pct, '', :task_id, :now, :actor
                    )
                """, params)
                row_id = int(result.lastrowid)
                _audit_structure(session, task, "Formation Added", None, str(row_id), actor, role,
                                 reason=row["formation"], correlation=correlation)
                for field_key in (
                    "formation", "top_tvdss_ft", "base_tvdss_ft", "thickness_ft",
                    "porosity_pct", "swt_pct", "pay_ft", "ngr_pct",
                ):
                    new_value = _audit_text(row.get(field_key))
                    if new_value:
                        _audit_structure(
                            session, task, "Formation Field Updated", "", new_value,
                            actor, role, reason=f"id={row_id};field={field_key}",
                            correlation=correlation,
                        )

            old_intervals = {item["id"]: item for item in (old_row or {}).get("pay_intervals", [])}
            supplied_interval_ids = {int(item["id"]) for item in row["pay_intervals"] if item.get("id") not in (None, "")}
            if not supplied_interval_ids.issubset(old_intervals):
                raise ValueError("A Pay Interval row is stale or belongs to another Formation.")
            removed_intervals = set(old_intervals) - supplied_interval_ids
            for interval_id in removed_intervals:
                _audit_structure(session, task, "Pay Interval Removed", str(interval_id), None, actor, role,
                                 reason=f"formation_id={row_id}", correlation=correlation)
            # seq is unique per (project, formation, phase) -- models.py:257 --
            # and SQLite enforces it statement by statement, so the renumbering
            # below cannot walk over rows that still hold the seq it is handing
            # out. Dropped intervals therefore go first (deleting the head of
            # the list frees seq 1 before the survivor moves into it), and the
            # survivors are then parked on a temporary seq no final value can
            # take, which is what a pure reorder needs (two rows swapping seq 1
            # and 2 collide in either order without it). ``-id`` is unique
            # table-wide, so parking stays safe even when a formation rename
            # moves intervals onto another name in the same save.
            if removed_intervals:
                db.execute(session, """
                    DELETE FROM project_formation_pay_intervals
                    WHERE id IN :removed AND project_id = :project_id AND phase = :phase
                """, {"removed": list(removed_intervals), "project_id": project_id, "phase": phase})
            if supplied_interval_ids:
                db.execute(session, """
                    UPDATE project_formation_pay_intervals SET seq = -id
                    WHERE id IN :kept AND project_id = :project_id AND phase = :phase
                """, {"kept": list(supplied_interval_ids), "project_id": project_id, "phase": phase})
            for seq, interval in enumerate(row["pay_intervals"], 1):
                interval_id = int(interval["id"]) if interval.get("id") not in (None, "") else None
                ip = dict(interval)
                ip.update({"project_id": project_id, "formation": row["formation"], "phase": phase,
                           "seq": seq, "task_id": task["task_id"], "now": now, "actor": actor})
                if interval_id:
                    db.execute(session, """
                        UPDATE project_formation_pay_intervals SET formation = :formation, seq = :seq,
                          top_tvdss_ft = :top_tvdss_ft, base_tvdss_ft = :base_tvdss_ft,
                          phit_pct = :phit_pct, swt_pct = :swt_pct, ngr_pct = :ngr_pct,
                          kint_md = :kint_md, fluid = :fluid, source_task_id = :task_id,
                          updated_at = :now, updated_by = :actor
                        WHERE id = :id AND project_id = :project_id AND phase = :phase
                    """, ip)
                    old_item = old_intervals[interval_id]
                    for field_key in ("seq", "top_tvdss_ft", "base_tvdss_ft", "phit_pct",
                                      "swt_pct", "ngr_pct", "kint_md", "fluid"):
                        old_value = _audit_text(old_item.get(field_key))
                        new_value = _audit_text(seq if field_key == "seq" else interval.get(field_key))
                        if old_value != new_value:
                            _audit_structure(session, task, "Pay Interval Field Updated",
                                             old_value, new_value, actor, role,
                                             reason=f"id={interval_id};field={field_key}",
                                             correlation=correlation)
                else:
                    result = db.execute(session, """
                        INSERT INTO project_formation_pay_intervals (
                          project_id, formation, phase, seq, top_tvdss_ft, base_tvdss_ft,
                          phit_pct, swt_pct, ngr_pct, kint_md, fluid, source_task_id, updated_at, updated_by
                        ) VALUES (
                          :project_id, :formation, :phase, :seq, :top_tvdss_ft, :base_tvdss_ft,
                          :phit_pct, :swt_pct, :ngr_pct, :kint_md, :fluid, :task_id, :now, :actor
                        )
                    """, ip)
                    interval_id = int(result.lastrowid)
                    _audit_structure(session, task, "Pay Interval Added", None, str(interval_id), actor, role,
                                     reason=f"formation_id={row_id}", correlation=correlation)
                    for field_key in ("seq", "top_tvdss_ft", "base_tvdss_ft", "phit_pct",
                                      "swt_pct", "ngr_pct", "kint_md", "fluid"):
                        new_value = _audit_text(seq if field_key == "seq" else interval.get(field_key))
                        if new_value:
                            _audit_structure(
                                session, task, "Pay Interval Field Updated", "", new_value,
                                actor, role, reason=f"id={interval_id};field={field_key}",
                                correlation=correlation,
                            )
        db.execute(session, """
            UPDATE project_tasks SET last_updated = :now, revision = revision + 1 WHERE task_id = :task_id
        """, {"now": now, "task_id": task["task_id"]})
        db.execute(session, """
            UPDATE projects SET last_updated = :now, revision = revision + 1 WHERE project_id = :project_id
        """, {"now": now, "project_id": project_id})
        refreshed_fields = _field_maps(session, project_id)
        refreshed_formations = _formation_rows(session, project_id)
        refreshed_effective = _effective_state(project, tasks, refreshed_fields, refreshed_formations)
        branch_error = _sad_branch_change_error(tasks, old_effective, refreshed_effective)
        if branch_error:
            raise ValueError(branch_error)
        _reconcile_system_state(session, project_id, actor, role, correlation)
        final_tasks = _task_map(session, project_id)
        final_effective = _effective_state(
            project, final_tasks, _field_maps(session, project_id), _formation_rows(session, project_id))
        _audit_effective_changes(
            session, final_tasks, old_effective, final_effective, actor, role, correlation,
            f"formations={phase}",
        )
    return get_detail(session, project_id, detail_slug)


FLOWBACK_KEYS = (
    "formation", "top_md", "base_md", "dynamic_area_km2", "dynamic_ogip_bcf",
    "gas_rate_mmscfd", "water_rate_bwpd", "liquid_rate_bpd", "choke_size_in", "fwhp_psi",
)


def _clean_flowback_rows(rows):
    if not isinstance(rows, list):
        raise ValueError("Flowback stages must be a list.")
    cleaned = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each Flowback stage must be an object.")
        stable_id = str(row.get("id") or row.get("_id") or uuid.uuid4()).strip()
        if stable_id in seen:
            raise ValueError("Duplicate Flowback stage identifier.")
        seen.add(stable_id)
        item = {"id": stable_id, "formation": str(row.get("formation") or "").strip()}
        for key in FLOWBACK_KEYS[1:]:
            raw = row.get(key)
            if raw is None or str(raw).strip() == "":
                item[key] = ""
            else:
                try:
                    Decimal(str(raw).strip())
                except InvalidOperation:
                    raise ValueError(f"Enter a valid number for {key.replace('_', ' ')}.")
                item[key] = str(raw).strip()
        cleaned.append(item)
    return cleaned


def save_flowback_stages(session, project_id, rows, actor="Web User", role="employee"):
    cleaned = _clean_flowback_rows(rows)
    project, tasks, fields, _formations, old_effective = _project_context(session, project_id)
    task = tasks.get("Flowback Results")
    old_rows = _flowback_rows(fields)
    old_by_id = {str(row.get("id") or row.get("_id") or ""): row for row in old_rows}
    new_by_id = {row["id"]: row for row in cleaned}
    correlation = str(uuid.uuid4())
    with db.write_transaction(session):
        for stable_id in new_by_id.keys() - old_by_id.keys():
            _audit_structure(session, task, "Flowback Stage Added", None, stable_id, actor, role,
                             reason="repeatable stage", correlation=correlation)
            for key in FLOWBACK_KEYS:
                new_value = _audit_text(new_by_id[stable_id].get(key))
                if new_value:
                    _audit_structure(
                        session, task, "Flowback Field Updated", "", new_value, actor, role,
                        reason=f"id={stable_id};field={key}", correlation=correlation,
                    )
        for stable_id in old_by_id.keys() - new_by_id.keys():
            _audit_structure(session, task, "Flowback Stage Removed", stable_id, None, actor, role,
                             reason="repeatable stage", correlation=correlation)
        for stable_id in new_by_id.keys() & old_by_id.keys():
            for key in FLOWBACK_KEYS:
                old_value = _audit_text(old_by_id[stable_id].get(key))
                new_value = _audit_text(new_by_id[stable_id].get(key))
                if old_value != new_value:
                    _audit_structure(session, task, "Flowback Field Updated",
                                     old_value, new_value, actor, role,
                                     reason=f"id={stable_id};field={key}", correlation=correlation)
        _set_field(session, task, "flowback_stages_rows",
                   json.dumps(cleaned, separators=(",", ":")), actor, role, "user",
                   "stable repeated stages", correlation)
        preview = _effective_state(
            project, tasks, _field_maps(session, project_id), _formation_rows(session, project_id))
        branch_error = _sad_branch_change_error(tasks, old_effective, preview)
        if branch_error:
            raise ValueError(branch_error)
        _reconcile_system_state(session, project_id, actor, role, correlation)
        final_tasks = _task_map(session, project_id)
        final_effective = _effective_state(
            project, final_tasks, _field_maps(session, project_id), _formation_rows(session, project_id))
        _audit_effective_changes(
            session, final_tasks, old_effective, final_effective, actor, role, correlation,
            "flowback stages",
        )
    return get_detail(session, project_id, "flowback-results")


def _system_field_task(tasks, task_name):
    task = tasks.get(task_name)
    if not task:
        raise ValueError(f"Required Business Plan component is missing: {task_name}.")
    return task


def _reconcile_system_state(session, project_id, actor, role, correlation):
    """Persist provenance/backups for reversible rules; effective states stay derived."""
    project = db.fetch_one(session, "SELECT * FROM projects WHERE project_id = :project_id",
                           {"project_id": project_id})
    tasks = _task_map(session, project_id)
    fields = _field_maps(session, project_id)
    formations = _formation_rows(session, project_id)
    effective = _effective_state(project, tasks, fields, formations)
    values = effective["values"]
    sad_task = _system_field_task(tasks, "SAD Update")
    pda_task = _system_field_task(tasks, "PDA")
    old_mode = _value(fields, "SAD Update", "bpe_sad_update_mode") or ""
    new_mode = effective["sad_update_branch"]
    if old_mode != new_mode:
        if old_mode == "copied_from_sad" and new_mode != "copied_from_sad":
            for _source_key, target_key in SAD_COPY_PAIRS:
                backup_key = "bpe_manual_backup_" + target_key
                backup = _value(fields, "SAD Update", backup_key)
                if backup is not None:
                    _set_field(session, sad_task, target_key, backup, actor, role, "system",
                               "restored manual value after copy branch", correlation)
        if new_mode == "copied_from_sad" and old_mode != "copied_from_sad":
            for source_key, target_key in SAD_COPY_PAIRS:
                current = _value(fields, "SAD Update", target_key)
                _set_field(session, sad_task, "bpe_manual_backup_" + target_key,
                           "" if current is None else current, actor, role, "system",
                           "preserved manual value before SAD copy", correlation,
                           force_create=True)
                _set_field(session, sad_task, target_key, values.get(source_key) or "", actor, role,
                           "system", "copied from SAD Model after Flowback comparison", correlation)
        _set_field(session, sad_task, "bpe_sad_update_mode", new_mode, actor, role, "system",
                   "Flowback/Quicklook decision", correlation)
        _audit_structure(session, sad_task, "SAD Update Branch Evaluated", old_mode, new_mode,
                         actor, role, source="system", reason="Flowback/Quicklook decision",
                         correlation=correlation, context={
                             "fluid_decision": effective["fluid"]["decision"],
                             "flowback_stage_count": len(effective["flowback_rows"]),
                             "comparison_input": effective["flowback_rows"][0]
                             if len(effective["flowback_rows"]) == 1 else None,
                             "sad_b90_area_km2": values.get("sad_area_km2_p90"),
                             "sad_mean_ogip_bcf": values.get("post_drill_piip_gas_mean"),
                         })

    old_fluid_mode = _value(fields, "PDA", "bpe_fluid_mode") or ""
    new_fluid_mode = effective["fluid"]["decision"]
    if old_fluid_mode != new_fluid_mode:
        if new_fluid_mode == "all_water_or_dry" and old_fluid_mode != "all_water_or_dry":
            _set_field(session, pda_task, "bpe_manual_booking_response",
                       values.get("reserves_booking_response") or "", actor, role, "system",
                       "preserved before automatic No", correlation)
            _set_field(session, pda_task, "bpe_manual_booking_year",
                       values.get("reserves_booking_year") or "", actor, role, "system",
                       "preserved before automatic No", correlation)
            _set_field(session, pda_task, "reserves_booking_response", "No", actor, role,
                       "system", "all Pay Intervals are Water Bearing or Dry Hole", correlation)
            _set_field(session, pda_task, "reserves_booking_year", "", actor, role,
                       "system", "automatic No clears active booking year", correlation)
        elif old_fluid_mode == "all_water_or_dry":
            prior_response = _value(fields, "PDA", "bpe_manual_booking_response") or ""
            prior_year = _value(fields, "PDA", "bpe_manual_booking_year") or ""
            _set_field(session, pda_task, "reserves_booking_response", prior_response, actor, role,
                       "system", "restored after Water Bearing/Dry Hole reversal", correlation)
            _set_field(session, pda_task, "reserves_booking_year", prior_year, actor, role,
                       "system", "restored after Water Bearing/Dry Hole reversal", correlation)
        _set_field(session, pda_task, "bpe_fluid_mode", new_fluid_mode, actor, role,
                   "system", "all-interval Fluid decision", correlation)
        _audit_structure(session, pda_task, "Quicklook Fluid Path Evaluated", old_fluid_mode,
                         new_fluid_mode, actor, role, source="system",
                         reason="all-interval Fluid decision", correlation=correlation,
                         context={"fluids": effective["fluid"]["fluids"]})


def get_field_options(session):
    rows = db.fetch_all(session, """
        SELECT project_name FROM projects
        WHERE archived = 0 AND (pipeline_type = 'bp' OR business_plan_enabled = 1)
    """)
    return sorted({_field_from_name(row["project_name"]) for row in rows if row.get("project_name")}, key=str.lower)
