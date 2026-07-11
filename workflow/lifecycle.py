"""Task reads, saves, assignment and the submit/approve/return transitions.

The v17 implicit lifecycle: Not Assigned -> In Progress (assignment) ->
Ready (submit) -> Approved (supervisor), with return sending Ready back to
In Progress. Every mutation is optimistic-lock guarded (StaleRevisionError
-> HTTP 409) and logged to task_history.
"""
from __future__ import annotations

from typing import Any, Dict

import cos
import db
from helpers import today_str, utc_now_str

from .constants import (
    DONE_STATUSES,
    STATUSES,
    TASK_TRANSITIONS,
    _TRANSITION_EVENTS,
    StaleRevisionError,
    applicable_stages,
)
from .history import log_task_event
from .projects import _sync_completed_at, get_project
from .users import find_active_user


def get_project_tasks(session, project_id):
    """Return the active task rows for a project, ordered by sequence."""
    return db.fetch_all(session, """
        SELECT * FROM project_tasks
        WHERE project_id = :project_id AND is_active = 1
        ORDER BY sequence_no
    """, {"project_id": project_id})


def get_task(session, task_id):
    """Return one task row dict, or None."""
    return db.fetch_one(session, "SELECT * FROM project_tasks WHERE task_id = :task_id",
                        {"task_id": task_id})


# ---------------------------------------------------------------------------
# Task priority + saves
# ---------------------------------------------------------------------------

def set_task_priority(session, task_id, priority_value="Medium", changed_by="Admin"):
    """Set a task's priority (Low/Medium/High) and log the change."""
    task = get_task(session, task_id)
    if not task:
        raise ValueError("Component not found.")
    if isinstance(priority_value, bool):
        new_priority = "High" if priority_value else "Medium"
    else:
        new_priority = str(priority_value or "Medium").strip().title()
    if new_priority not in {"Low", "Medium", "High"}:
        new_priority = "Medium"
    old_priority = task.get("priority") or "Medium"
    if new_priority == old_priority:
        return
    with db.write_transaction(session):
        db.execute(session, """
            UPDATE project_tasks
            SET priority = :priority, last_updated = :now
            WHERE task_id = :task_id
        """, {"priority": new_priority, "now": utc_now_str(), "task_id": task_id})
        log_task_event(
            session,
            task_id=task_id,
            project_id=task["project_id"],
            task_name=task["task_name"],
            action_type="Priority Update",
            old_status=old_priority,
            new_status=new_priority,
            changed_by=changed_by,
            comment=f"Priority set to {new_priority}.",
        )


def get_task_dynamic_fields(session, task_id):
    """Return {field_key: field_value} for a task's dynamic fields."""
    rows = db.fetch_all(session,
                        "SELECT field_key, field_value FROM task_dynamic_fields WHERE task_id = :task_id",
                        {"task_id": task_id})
    return {r["field_key"]: r["field_value"] for r in rows}


def _apply_dynamic_fields(session, task, fields, changed_by, now):
    """Upsert dynamic fields and log a history note for the changed keys.

    Shared by :func:`save_task` and :func:`save_task_dynamic_fields` so the
    field-upsert + history-note logic exists in exactly one place. Does not
    commit or touch task status/revision. There is no overview mirror: the
    /detail overview is composed from these fields at read time
    (get_project_overview).
    """
    task_id = task["task_id"]
    changed_keys = []
    for key, value in fields.items():
        val = "" if value is None else str(value).strip()
        existing = db.fetch_one(session,
                                "SELECT field_value FROM task_dynamic_fields WHERE task_id = :task_id AND field_key = :field_key",
                                {"task_id": task_id, "field_key": key})
        old_val = "" if not existing or existing["field_value"] is None else str(existing["field_value"])
        if old_val != val:
            changed_keys.append(key)
        db.execute(session, """
            INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
            VALUES (:task_id, :field_key, :field_value, :now)
            ON CONFLICT(task_id, field_key) DO UPDATE
            SET field_value = excluded.field_value, updated_at = excluded.updated_at
        """, {"task_id": task_id, "field_key": key, "field_value": val, "now": now})
    if changed_keys:
        readable = [key.replace("_", " ") for key in changed_keys]
        listed = ", ".join(readable[:8])
        if len(readable) > 8:
            listed += ", and more"
        log_task_event(session, task_id, task["project_id"], task["task_name"], "Component Inputs Updated",
                       None, None, changed_by, f"Updated inputs: {listed}.")


def save_task_dynamic_fields(session, task_id, fields, changed_by="Web User"):
    """Save a task's dynamic fields only (no status change, no revision check).

    Seal CoS is recomputed on save. The Total Chance of Success needs no
    recalculation trigger: it is computed at read time (calculate_total_cos)
    from the stored Reservoir/Trap/Seal CoS inputs.
    """
    task = get_task(session, task_id)
    if not task:
        raise ValueError("Component not found.")
    fields = fields or {}
    if task.get("task_name") == "Seal CoS":
        fields = dict(fields)
        fields["seal_cos_pct"] = cos.calculate_seal_cos(fields)
    now = utc_now_str()
    with db.write_transaction(session):
        _apply_dynamic_fields(session, task, fields, changed_by, now)
        db.execute(session, "UPDATE project_tasks SET last_updated = :now WHERE task_id = :task_id",
                   {"now": now, "task_id": task_id})


def save_task(session, task_id, payload, changed_by="Web User"):
    """Save a component atomically: fields, priority, status and workflow state.

    ``revision`` is optional for backward compatibility. When provided, stale
    edits are rejected (StaleRevisionError -> HTTP 409) rather than silently
    overwriting a newer change. Preserves the actual_start/actual_finish rules
    and the multiple project-revision bumps of the original implementation.
    """
    payload = payload or {}
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    expected_revision = payload.get("revision")
    # ``status`` is optional (the v17 UI drives status via /assign and
    # /transition; Save only persists inputs). When supplied it must be one of
    # the four STATUSES; anything else (including legacy names) is rejected at
    # the API boundary.
    status_supplied = payload.get("status") is not None
    status = str(payload.get("status") or "").strip() if status_supplied else None
    if status_supplied and status not in STATUSES:
        raise ValueError("Invalid component status.")
    assigned_to_supplied = "assigned_to" in payload
    assigned_to = str(payload.get("assigned_to") or "").strip()
    comments = str(payload.get("comments") or "").strip()
    priority = str(payload.get("priority") or "Medium").strip().title()
    if priority not in {"Low", "Medium", "High"}:
        priority = "Medium"

    result: Dict[str, Any] = {}
    with db.write_transaction(session):
        task = get_task(session, task_id)
        if not task:
            raise ValueError("Component not found.")
        if not status_supplied:
            status = task.get("status") or "Not Assigned"
        if not assigned_to_supplied:
            # Assignment is managed by assign_task; a Save without the key must
            # not clear the assignee.
            assigned_to = (task.get("assigned_to") or "").strip()
        current_revision = int(task.get("revision") or 0)
        if expected_revision is not None:
            try:
                if int(expected_revision) != current_revision:
                    raise StaleRevisionError("This component was updated by someone else. Refresh and review the latest values.")
            except (TypeError, ValueError):
                raise ValueError("Invalid component revision.")

        old_status = task.get("status") or "Not Assigned"
        old_assigned_to = (task.get("assigned_to") or "").strip()
        old_comments = (task.get("comments") or "").strip()
        old_priority = task.get("priority") or "Medium"
        actual_start = task.get("actual_start")
        actual_finish = task.get("actual_finish")
        today = today_str()
        now = utc_now_str()
        if status == "In Progress" and not actual_start:
            actual_start = today
        if status in DONE_STATUSES:
            actual_finish = today
            if not actual_start:
                actual_start = today
        else:
            actual_finish = None
            if status == "Not Assigned":
                actual_start = None

        project = get_project(session, task["project_id"]) or {}
        bp_enabled = payload.get("business_plan_enabled")
        if bp_enabled is None:
            bp_enabled = task.get("business_plan_enabled") or project.get("business_plan_enabled") or 0
        enabled_int = 1 if bool(bp_enabled) else 0
        year_val = None
        if enabled_int:
            selected_year = payload.get("business_plan_year") or task.get("business_plan_year") or project.get("business_plan_year")
            year_val = int(selected_year) if selected_year else None
            if year_val is None or year_val < 2026 or year_val > 2040:
                raise ValueError("Select a business plan year from 2026 to 2040.")

        # Reservoir CoS is model-derived, not manually keyed. The saved result is a whole-number percent.
        if task.get("task_name") == "Reservoir CoS" and "reservoir_cos_rows" in fields:
            fields = dict(fields)
            fields["reservoir_cos_rows"] = cos.calculate_reservoir_cos_rows(fields.get("reservoir_cos_rows"))

        # Seal CoS is formula-derived, not manually entered. The result is stored
        # as a whole-number percentage string, e.g., 44 for 44%.
        if task.get("task_name") == "Seal CoS":
            fields = dict(fields)
            fields["seal_cos_pct"] = cos.calculate_seal_cos(fields)

        _apply_dynamic_fields(session, task, fields, changed_by, now)

        update_result = db.execute(session, """
            UPDATE project_tasks
            SET status = :status, assigned_to = :assigned_to, comments = :comments, priority = :priority,
                actual_start = :actual_start, actual_finish = :actual_finish,
                business_plan_enabled = :bp_enabled, business_plan_year = :bp_year,
                last_updated = :now, revision = revision + 1
            WHERE task_id = :task_id AND revision = :expected_revision
        """, {"status": status, "assigned_to": assigned_to or None, "comments": comments or None,
              "priority": priority, "actual_start": actual_start, "actual_finish": actual_finish,
              "bp_enabled": enabled_int, "bp_year": year_val, "now": now,
              "task_id": task_id, "expected_revision": current_revision})
        if update_result.rowcount != 1:
            raise StaleRevisionError("This component was updated by someone else. Refresh and review the latest values.")

        if enabled_int:
            db.execute(session, """
                UPDATE projects
                SET business_plan_enabled = 1, business_plan_year = :bp_year, last_updated = :now, revision = revision + 1
                WHERE project_id = :project_id
            """, {"bp_year": year_val, "now": now, "project_id": task["project_id"]})

        if status != old_status or assigned_to != old_assigned_to or comments != old_comments or priority != old_priority:
            log_task_event(session, task_id, task["project_id"], task["task_name"], "Component Update",
                           old_status, status, changed_by, comments or f"Status set to {status}.")

        # A status change may have completed or reopened the applicable set;
        # the board pointers themselves are derived at read time.
        _sync_completed_at(session, task["project_id"])
        db.execute(session,
                   "UPDATE projects SET last_updated = :now, revision = revision + 1 WHERE project_id = :project_id",
                   {"now": now, "project_id": task["project_id"]})
        result = get_task(session, task_id) or {}
    return result


def _check_expected_revision(task, expected_revision):
    """Shared optimistic-lock precheck: mirror save_task's semantics exactly.

    None means "no check requested". A non-integer -> ValueError (400); a
    mismatch -> StaleRevisionError (409).
    """
    if expected_revision is None:
        return
    current_revision = int(task.get("revision") or 0)
    try:
        supplied = int(expected_revision)
    except (TypeError, ValueError):
        raise ValueError("Invalid component revision.")
    if supplied != current_revision:
        raise StaleRevisionError("This component was updated by someone else. Refresh and review the latest values.")


def assign_task(session, task_id, assignee, cascade=True, changed_by="Web User", expected_revision=None):
    """Assign a component to an active user; optionally cascade to later steps.

    The v17 lifecycle has no manual status field: assignment IS the act that
    moves a step from "Not Assigned" to "In Progress".

    - ``assignee`` must match an active row in the users table
      (case-insensitive); the canonical casing from the table is stored.
    - Target task: assigned_to is set; a "Not Assigned" status becomes
      "In Progress" (other statuses are a pure reassignment and keep their
      status).
    - ``cascade``: every SUBSEQUENT (sequence_no greater than the target's)
      active task in the project's applicable pipeline stages that is still
      "Not Assigned" receives the same assignee and moves to "In Progress".
      Rows already In Progress / Ready / Approved are never touched, and rows
      outside the applicable pipeline stages are never in scope.
    - Optimistic locking: ``expected_revision`` is checked against the TARGET
      task only, and the target's UPDATE is itself revision-guarded like
      save_task/transition_task (StaleRevisionError -> 409). Every changed row
      gets a revision bump and one "Component Assigned" history event.

    Returns the fresh target task row (same shape as save_task) so the UI can
    adopt the new revision.
    """
    result: Dict[str, Any] = {}
    with db.write_transaction(session):
        task = get_task(session, task_id)
        if not task:
            raise ValueError("Component not found.")
        user = find_active_user(session, assignee)
        if not user:
            raise ValueError("Unknown or inactive user.")
        canonical_name = user["name"]
        _check_expected_revision(task, expected_revision)

        project = get_project(session, task["project_id"]) or {}
        stages = applicable_stages(project.get("pipeline_type"))
        now = utc_now_str()
        today = today_str()

        targets = [task]
        if cascade:
            targets += db.fetch_all(session, """
                SELECT * FROM project_tasks
                WHERE project_id = :project_id AND is_active = 1
                  AND sequence_no > :sequence_no AND stage_group IN :stages
                  AND status = 'Not Assigned'
                ORDER BY sequence_no
            """, {"project_id": task["project_id"], "sequence_no": task["sequence_no"],
                  "stages": stages})

        for row in targets:
            old_status = row["status"] or "Not Assigned"
            new_status = "In Progress" if old_status == "Not Assigned" else old_status
            # Same stamping rule as save_task: a task entering In Progress gets
            # actual_start today (never overwriting an existing date).
            actual_start = row.get("actual_start") or (today if new_status == "In Progress" else None)
            # The TARGET row's update is guarded on the revision read in this
            # transaction (same WHERE revision + rowcount pattern as save_task /
            # transition_task). Under SQLite this is belt-and-braces -- BEGIN
            # IMMEDIATE already serializes writers -- but it keeps all three
            # mutation paths on one pattern for a Postgres future, where MVCC
            # would allow a concurrent commit between our read and this write.
            # Cascade rows stay unguarded: they were selected inside this same
            # transaction and carry no client-supplied revision to honor.
            is_target = row["task_id"] == task["task_id"]
            sql = """
                UPDATE project_tasks
                SET assigned_to = :assignee, status = :status, actual_start = :actual_start,
                    last_updated = :now, revision = COALESCE(revision, 0) + 1
                WHERE task_id = :task_id
            """
            params = {"assignee": canonical_name, "status": new_status, "actual_start": actual_start,
                      "now": now, "task_id": row["task_id"]}
            if is_target:
                sql += " AND COALESCE(revision, 0) = :current_revision"
                params["current_revision"] = int(row.get("revision") or 0)
            update_result = db.execute(session, sql, params)
            if is_target and update_result.rowcount != 1:
                raise StaleRevisionError("This component was updated by someone else. Refresh and review the latest values.")
            log_task_event(session, row["task_id"], row["project_id"], row["task_name"],
                           "Component Assigned", old_status, new_status, changed_by,
                           f"Assigned to {canonical_name}.")

        # No completed_at sync: assignment only moves Not Assigned ->
        # In Progress, which can never complete or reopen the applicable set
        # (a complete set has no Not Assigned rows to assign).
        db.execute(session,
                   "UPDATE projects SET last_updated = :now, revision = revision + 1 WHERE project_id = :project_id",
                   {"now": now, "project_id": task["project_id"]})
        result = get_task(session, task_id) or {}
    return result


def transition_task(session, task_id, action, changed_by="Web User", expected_revision=None,
                    actor_role=None, actor_name=None):
    """Advance a component through the v17 lifecycle: submit / approve / return.

    - ``submit``: "In Progress" -> "Ready". Supervisors/staff may submit any
      component; an 'employee' may only submit a component assigned to them
      (case-insensitive name match against ``actor_name`` -> PermissionError
      / 403 otherwise). The supervisor-only gates for approve/return live in
      the route (require_role).
    - ``approve``: "Ready" -> "Approved" (stamps actual_finish, backfills
      actual_start like save_task does for done statuses).
    - ``return``: "Ready" -> "In Progress" (clears actual_finish if set).

    Wrong from-state or an unknown action -> ValueError (400). Optimistic
    locking mirrors save_task (StaleRevisionError -> 409). One history event is
    logged with the old/new status; completed_at is re-synced (approve can
    complete the applicable set, return reopens it). Returns the fresh task
    row.
    """
    action_key = str(action or "").strip().lower()
    if action_key not in TASK_TRANSITIONS:
        raise ValueError("Unknown action. Use one of: submit, approve, return.")
    required_status, new_status = TASK_TRANSITIONS[action_key]

    result: Dict[str, Any] = {}
    with db.write_transaction(session):
        task = get_task(session, task_id)
        if not task:
            raise ValueError("Component not found.")
        if action_key == "submit" and actor_role == "employee":
            assigned = (task.get("assigned_to") or "").strip().lower()
            if assigned != (actor_name or "").strip().lower():
                raise PermissionError("Forbidden: you can only submit components assigned to you.")
        _check_expected_revision(task, expected_revision)

        old_status = task.get("status") or "Not Assigned"
        if old_status != required_status:
            raise ValueError(
                f'Cannot {action_key} a component in status "{old_status}" -- it must be "{required_status}".')

        today = today_str()
        now = utc_now_str()
        actual_start = task.get("actual_start")
        actual_finish = task.get("actual_finish")
        if new_status in DONE_STATUSES:
            actual_finish = today
            if not actual_start:
                actual_start = today
        else:
            actual_finish = None

        current_revision = int(task.get("revision") or 0)
        update_result = db.execute(session, """
            UPDATE project_tasks
            SET status = :status, actual_start = :actual_start, actual_finish = :actual_finish,
                last_updated = :now, revision = revision + 1
            WHERE task_id = :task_id AND revision = :expected_revision
        """, {"status": new_status, "actual_start": actual_start, "actual_finish": actual_finish,
              "now": now, "task_id": task_id, "expected_revision": current_revision})
        if update_result.rowcount != 1:
            raise StaleRevisionError("This component was updated by someone else. Refresh and review the latest values.")

        log_task_event(session, task_id, task["project_id"], task["task_name"],
                       _TRANSITION_EVENTS[action_key], old_status, new_status, changed_by,
                       f"Status moved from {old_status} to {new_status}.")

        # Approve may have completed the applicable set; return reopens it.
        _sync_completed_at(session, task["project_id"])
        db.execute(session,
                   "UPDATE projects SET last_updated = :now, revision = revision + 1 WHERE project_id = :project_id",
                   {"now": now, "project_id": task["project_id"]})
        result = get_task(session, task_id) or {}
    return result
