"""Lead-summary snapshots + BP promotion / demotion (+ the project flags)."""
from __future__ import annotations

import json
from datetime import date
from typing import Dict

import db
from helpers import utc_now_str

from .constants import BP_EXECUTION_STAGES, PROSPECT_STAGES
from .history import log_task_event
from .projects import _sync_completed_at, get_project


def get_lead_summary_snapshot(session, project_id: int):
    """Return the captured lead-summary snapshot dict for a project, or None."""
    row = db.fetch_one(session,
                       "SELECT snapshot_json, captured_at, captured_by FROM lead_summary_snapshots WHERE project_id = :project_id",
                       {"project_id": project_id})
    if not row:
        return None
    try:
        fields = json.loads(row["snapshot_json"] or "{}")
    except json.JSONDecodeError:
        fields = {}
    return {"fields": fields, "captured_at": row["captured_at"], "captured_by": row["captured_by"]}


def _capture_lead_summary_snapshot(session, project_id: int, changed_by: str):
    """Capture the Lead Summary immediately before promotion to BP Execution.

    Re-promotion refreshes the snapshot so the BP Well always carries the current
    Lead Summary that was moved with it.
    """
    rows = db.fetch_all(session, """
        SELECT pt.task_name, tdf.field_key, tdf.field_value
        FROM project_tasks pt
        LEFT JOIN task_dynamic_fields tdf ON tdf.task_id = pt.task_id
        WHERE pt.project_id = :project_id AND pt.stage_group IN :stages
        ORDER BY pt.sequence_no, tdf.field_key
    """, {"project_id": project_id, "stages": PROSPECT_STAGES})
    fields: Dict[str, Dict[str, str]] = {}
    for row in rows:
        fields.setdefault(row["task_name"], {})
        if row["field_key"]:
            fields[row["task_name"]][row["field_key"]] = row["field_value"] or ""
    db.execute(session, """
        INSERT INTO lead_summary_snapshots(project_id, snapshot_json, captured_at, captured_by)
        VALUES (:project_id, :snapshot_json, :captured_at, :captured_by)
        ON CONFLICT(project_id) DO UPDATE SET
            snapshot_json = excluded.snapshot_json,
            captured_at = excluded.captured_at,
            captured_by = excluded.captured_by
    """, {"project_id": project_id, "snapshot_json": json.dumps(fields, separators=(",", ":")),
          "captured_at": utc_now_str(), "captured_by": changed_by})


def _move_lead_to_bp_execution(session, project_id: int, year_val: int, changed_by: str):
    """Promote a matured lead into the BP Execution pipeline without losing its lead record.

    Applicability is derived from pipeline_type (see applicable_stages), and
    the board pointers are derived at read time, so promotion is a PURE
    pipeline switch: it never rewrites task statuses, data or pointers. The
    BP-stage rows -- including any progress entered before promotion -- carry
    through untouched. Only the project's pipeline_type and BP flags/year
    move, plus the lead-summary snapshot capture.
    """
    project = get_project(session, project_id)
    if not project:
        raise ValueError("Lead / well not found.")
    if str(project.get("pipeline_type") or "prospect").lower() != "bp":
        _capture_lead_summary_snapshot(session, project_id, changed_by)

    bp_task_count = db.fetch_one(session, """
        SELECT COUNT(*) AS c FROM project_tasks
        WHERE project_id = :project_id AND stage_group IN :stages AND is_active = 1
    """, {"project_id": project_id, "stages": BP_EXECUTION_STAGES})["c"]
    if not bp_task_count:
        raise RuntimeError("Business Plan workflow is not available for this lead.")
    db.execute(session, """
        UPDATE projects
        SET pipeline_type = 'bp', business_plan_enabled = 1, business_plan_year = :year,
            last_updated = :now, revision = COALESCE(revision, 0) + 1
        WHERE project_id = :project_id
    """, {"year": year_val, "now": utc_now_str(), "project_id": project_id})


def _move_bp_to_lead_phase(session, project_id: int, changed_by: str):
    """Return a promoted BP Well to the lead phase without data loss.

    The reverse of promotion, and equally pure: only the project's
    pipeline_type and BP flags/year move. Task rows and their data (including
    entered BP progress) survive untouched; the board pointers re-derive from
    the prospect stages on the next read. That derivation is what routes the
    two recall outcomes: a fully matured record (every prospect step Approved)
    derives as Completed, so it stays OFF the maturation board and inside the
    Portfolio's mature-lead arm, while a record promoted before maturation
    finished returns to the board exactly where it left off.
    """
    project = get_project(session, project_id)
    if not project:
        raise ValueError("Lead / well not found.")
    db.execute(session, """
        UPDATE projects
        SET pipeline_type = 'prospect', business_plan_enabled = 0,
            business_plan_year = NULL, last_updated = :now,
            revision = COALESCE(revision, 0) + 1
        WHERE project_id = :project_id
    """, {"now": utc_now_str(), "project_id": project_id})


def set_business_plan(session, project_id, enabled, year=None, changed_by="Admin",
                       *args, allow_historical_year=False, **kwargs):
    """Enable/disable the Business Plan for a project (promotion / demotion)."""
    old = get_project(session, project_id)
    if not old:
        raise ValueError("Lead / well not found.")
    enabled_int = 1 if enabled else 0
    year_val = None
    if enabled_int:
        year_val = int(year or old.get("business_plan_year") or 0)
        # Newly enabling a record (promotion) can't target a past year: check
        # against the stored flag, not `enabled`, so a year-only edit of an
        # already-enabled well keeps the wider 1990-2040 window below. Excel
        # imports legitimately enable BP wells with historical years through
        # this same path, hence the escape hatch. This strict window is
        # evaluated FIRST so a newly-enabling out-of-range year gets this
        # message, not the wide one below.
        was_enabled = bool(old.get("business_plan_enabled"))
        if not was_enabled and not allow_historical_year:
            current_year = date.today().year
            if year_val < current_year or year_val > 2035:
                raise ValueError(f"Select a business plan year from {current_year} to 2035.")
        # Floor is 1990, not 2026: this window still admits imported historical
        # wells (drilled pre-2026) via allow_historical_year above. The promote
        # dialog UI never offers a year outside this range either.
        elif year_val < 1990 or year_val > 2040:
            raise ValueError("Select a business plan year from 1990 to 2040.")
    with db.write_transaction(session):
        if enabled_int:
            _move_lead_to_bp_execution(session, project_id, year_val, changed_by)
        else:
            # Removing Business Plan returns the record to the Lead pipeline.
            # No BP values, task data, Lead Summary snapshot, or history is deleted.
            _move_bp_to_lead_phase(session, project_id, changed_by)
        first_task = db.fetch_one(session,
                                  "SELECT task_id, task_name FROM project_tasks WHERE project_id = :project_id ORDER BY sequence_no LIMIT 1",
                                  {"project_id": project_id})
        if first_task:
            old_state = f"{old.get('business_plan_enabled') or 0}/{old.get('business_plan_year') or '-'}"
            new_state = f"{enabled_int}/{year_val or '-'}"
            action = "Lead Promoted to BP Execution" if enabled_int and str(old.get("pipeline_type") or "prospect").lower() != "bp" else ("Well Added to BP" if enabled_int else "BP Well Returned to Lead Phase")
            log_task_event(session, first_task["task_id"], project_id, first_task["task_name"], action, old_state, new_state, changed_by, "Business plan assignment updated.")
        # The applicable set just changed pipelines, which can complete or
        # reopen the project (e.g. a fully-approved prospect promoted into an
        # unstarted BP pipeline is no longer complete).
        _sync_completed_at(session, project_id)


def update_project_flags(session, project_id, business_plan_enabled=None, active_well_enabled=None, business_plan_year=None, changed_by="Web User", allow_historical_year=False, active_drilling=None):
    """Apply BP promotion/demotion and/or the per-well flags for a project."""
    old = get_project(session, project_id)
    if not old:
        raise ValueError("Lead / well not found.")
    # Promotion is an atomic business operation: capture lead summary, switch pipeline, activate BP tasks.
    if business_plan_enabled is not None:
        requested_year = business_plan_year if business_plan_enabled else None
        set_business_plan(session, project_id, bool(business_plan_enabled), requested_year, changed_by,
                          allow_historical_year=allow_historical_year)
    if active_well_enabled is not None:
        with db.write_transaction(session):
            new_active = 1 if active_well_enabled else 0
            db.execute(session,
                       "UPDATE projects SET active_well_enabled = :active, last_updated = :now, revision = COALESCE(revision, 0) + 1 WHERE project_id = :project_id",
                       {"active": new_active, "now": utc_now_str(), "project_id": project_id})
            first_task = db.fetch_one(session,
                                      "SELECT task_id, task_name FROM project_tasks WHERE project_id = :project_id ORDER BY sequence_no LIMIT 1",
                                      {"project_id": project_id})
            if first_task and new_active != int(old.get("active_well_enabled") or 0):
                log_task_event(session, first_task["task_id"], project_id, first_task["task_name"], "Active Well Flag", old.get("active_well_enabled") or 0, new_active, changed_by, "Active well flag updated.")
    if active_drilling is not None:
        _set_active_drilling(session, project_id, bool(active_drilling), changed_by)


# ---------------------------------------------------------------------------
# Card 3X -- Active Drilling
# ---------------------------------------------------------------------------

# The flag has been stored here since before this card: a dynamic field on the
# well's Quicklook Logs row, which the board already reads (see
# workflow.projects.get_projects' active_drilling subquery). Reusing it means no
# migration and no data to move -- and it IS canonical server state, which is
# what the card asks for, as opposed to something the browser remembers.
ACTIVE_DRILLING_FIELD = "active_drilling"
ACTIVE_DRILLING_EVENT = "Active Drilling Flag"
_ACTIVE_DRILLING_STEPS = ("Quicklook Logs", "Quicklook Logs Interpretation")


def active_drilling_state(session, project_id):
    """Is this well flagged as actively drilling? (any owning row counts)"""
    row = db.fetch_one(session, """
        SELECT MAX(CASE WHEN LOWER(COALESCE(d.field_value, '')) IN ('1', 'true', 'yes', 'on')
                        THEN 1 ELSE 0 END) AS flag
        FROM project_tasks t
        JOIN task_dynamic_fields d ON d.task_id = t.task_id
        WHERE t.project_id = :project_id AND d.field_key = :key
    """, {"project_id": project_id, "key": ACTIVE_DRILLING_FIELD})
    return bool(row and int(row.get("flag") or 0))


def active_drilling_allowed(session, project_id):
    """May this record be marked as actively drilling?

    Only a Business Plan well whose CURRENT BPE stage is Post-Drilling: a well
    that has not finished Pre-Drilling has not spudded, and one in Post-Testing
    has finished. The import is local because workflow.business_plan imports
    this module (for the lead snapshot); both sides only touch the other at call
    time, so the cycle never runs at import.
    """
    from .business_plan import current_stage_key
    try:
        return current_stage_key(session, project_id) == "post_drilling"
    except ValueError:
        # Not a BP record at all -- a lead cannot be drilling either.
        return False


def _set_active_drilling(session, project_id, enabled, changed_by):
    """Persist the flag and audit the CHANGE.

    An unchanged state writes nothing: repeatedly saving "still drilling" is
    not an event, and a trail full of no-ops hides the toggles that mattered.
    """
    previous = active_drilling_state(session, project_id)
    # Enforced HERE, not only in the two gear menus that offer the checkbox: a
    # rule about which wells can be drilling is a rule about the data, and a
    # direct PATCH must meet it too. Turning the flag OFF is always allowed --
    # a well that moved on should not be stuck reading "drilling".
    if enabled and not previous and not active_drilling_allowed(session, project_id):
        raise ValueError(
            "Only a well in the Post-Drilling stage can be marked as actively drilling.")
    task = db.fetch_one(session, f"""
        SELECT task_id, task_name FROM project_tasks
        WHERE project_id = :project_id
          AND task_name IN ({", ".join(f"'{name}'" for name in _ACTIVE_DRILLING_STEPS)})
        ORDER BY sequence_no LIMIT 1
    """, {"project_id": project_id})
    if not task:
        raise ValueError("This record has no Quicklook Logs step to record drilling against.")
    if previous == bool(enabled):
        return previous
    now = utc_now_str()
    with db.write_transaction(session):
        db.execute(session, """
            INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
            VALUES (:task_id, :key, :value, :now)
            ON CONFLICT(task_id, field_key) DO UPDATE
            SET field_value = excluded.field_value, updated_at = excluded.updated_at
        """, {"task_id": task["task_id"], "key": ACTIVE_DRILLING_FIELD,
              "value": "1" if enabled else "0", "now": now})
        log_task_event(session, task["task_id"], project_id, task["task_name"],
                       ACTIVE_DRILLING_EVENT, "1" if previous else "0",
                       "1" if enabled else "0", changed_by,
                       "Active Drilling turned " + ("on." if enabled else "off."))
    return bool(enabled)
