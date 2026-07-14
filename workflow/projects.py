"""Project CRUD and the derived board state.

Creation materializes one ``project_tasks`` row per PIPELINE_TEMPLATES step;
the board pointers (current stage/task/owner/status) are DERIVED from the
task rows at read time -- never stored (see _annotate_derived_state).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from sqlalchemy.exc import IntegrityError

import config
import db
import folders
from helpers import health_from_target, parse_iso_date, today_str, utc_now_str

from .constants import BP_EXECUTION_STAGES, PIPELINE_TEMPLATES, STAGE_ORDER, applicable_stages
from .history import log_task_event


# ---------------------------------------------------------------------------
# Project creation
# ---------------------------------------------------------------------------

def add_project(session, project_name, start_date=None, target_date=None, changed_by="System", lead_x=None, lead_y=None,
                business_plan_year=None, business_plan_enabled=False, active_well_enabled=False, pipeline_type="prospect"):
    """Create a project and materialize its 31 workflow tasks; return project_id."""
    project_name = (project_name or '').strip()
    if not project_name:
        raise ValueError("Lead / well name is required.")
    if len(project_name) > 120:
        raise ValueError("Lead / well name must be 120 characters or less.")
    pipeline_type = str(pipeline_type or "prospect").strip().lower()
    if pipeline_type not in {"prospect", "bp"}:
        pipeline_type = "prospect"
    now = utc_now_str()
    start_date = start_date or today_str()
    target_date = target_date or ""
    if business_plan_year:
        try:
            year_val = int(business_plan_year)
        except (TypeError, ValueError):
            raise ValueError("Select a business plan year from 2026 to 2040.")
    else:
        year_val = None
    bp_enabled = 1 if business_plan_enabled or year_val else 0
    if bp_enabled and (year_val is None or year_val < 2026 or year_val > 2040):
        raise ValueError("Select a business plan year from 2026 to 2040.")

    # Friendly duplicate check up front; the IntegrityError catch below still
    # covers the race where another request inserts the same name in between.
    duplicate = db.fetch_one(session,
                             "SELECT 1 AS present FROM projects WHERE project_name = :project_name",
                             {"project_name": project_name})
    if duplicate:
        raise ValueError("A lead / well with this name already exists.")

    # The workflow definition lives in code (PIPELINE_TEMPLATES); the creation
    # history event anchors on the pipeline's first step.
    first_template = (next((t for t in PIPELINE_TEMPLATES if t[2] in BP_EXECUTION_STAGES), PIPELINE_TEMPLATES[0])
                      if pipeline_type == "bp" else PIPELINE_TEMPLATES[0])

    try:
        return _insert_project_with_tasks(session, project_name, start_date, target_date, changed_by,
                                          lead_x, lead_y, year_val, bp_enabled, active_well_enabled,
                                          pipeline_type, first_template, now)
    except IntegrityError as exc:
        # UNIQUE(project_name) race lost to a concurrent insert.
        if "unique" in str(getattr(exc, "orig", None) or exc).lower():
            raise ValueError("A lead / well with this name already exists.") from exc
        raise


def _insert_project_with_tasks(session, project_name, start_date, target_date, changed_by, lead_x, lead_y,
                               year_val, bp_enabled, active_well_enabled, pipeline_type,
                               first_template, now):
    """Insert the project row plus one task per PIPELINE_TEMPLATES step in one locked transaction."""
    first_sequence, first_task_name, _first_stage = first_template
    with db.write_transaction(session):
        result = db.execute(session, """
            INSERT INTO projects (
                project_name, start_date, target_date, last_updated,
                lead_folder_path, lead_x, lead_y, business_plan_enabled, business_plan_year,
                active_well_enabled, pipeline_type
            ) VALUES (:project_name, :start_date, :target_date, :last_updated,
                      :lead_folder_path, :lead_x, :lead_y, :business_plan_enabled, :business_plan_year,
                      :active_well_enabled, :pipeline_type)
        """, {
            "project_name": project_name, "start_date": start_date,
            "target_date": target_date, "last_updated": now,
            "lead_folder_path": folders.default_lead_folder_path(project_name),
            "lead_x": lead_x or None, "lead_y": lead_y or None,
            "business_plan_enabled": bp_enabled, "business_plan_year": year_val,
            "active_well_enabled": 1 if active_well_enabled else 0, "pipeline_type": pipeline_type,
        })
        project_id = result.lastrowid  # PG: use RETURNING when on Postgres
        first_task_id = None
        for sequence_no, task_name, stage_group in PIPELINE_TEMPLATES:
            # Every step starts Not Assigned regardless of pipeline_type;
            # assignment moves it to In Progress. Applicability is derived per
            # pipeline at query time (applicable_stages), never stored per row,
            # so all 31 rows are materialized identically.
            initial_status = "Not Assigned"
            task_result = db.execute(session, """
                INSERT INTO project_tasks (
                    project_id, sequence_no, task_name, stage_group, assigned_to,
                    status, actual_start, actual_finish, comments, priority, business_plan_enabled,
                    business_plan_year, is_active, last_updated
                ) VALUES (:project_id, :sequence_no, :task_name, :stage_group, :assigned_to,
                          :status, :actual_start, :actual_finish, :comments, :priority, :business_plan_enabled,
                          :business_plan_year, 1, :last_updated)
            """, {
                "project_id": project_id,
                "sequence_no": sequence_no, "task_name": task_name,
                "stage_group": stage_group, "assigned_to": None,
                "status": initial_status,
                "actual_start": None, "actual_finish": None,
                "comments": None, "priority": "Medium",
                "business_plan_enabled": bp_enabled, "business_plan_year": year_val,
                "last_updated": now,
            })
            if sequence_no == first_sequence:
                first_task_id = task_result.lastrowid  # PG: use RETURNING when on Postgres

        if first_task_id is not None:
            action = "Well Added to BP" if pipeline_type == "bp" else "Lead Created"
            comment = f"{'Well added to Business Plan Execution' if pipeline_type == 'bp' else 'Lead created'}: {project_name}"
            log_task_event(
                session,
                task_id=first_task_id,
                project_id=project_id,
                task_name=first_task_name,
                action_type=action,
                old_status=None,
                new_status="Created",
                changed_by=changed_by,
                comment=comment,
            )
    return project_id


# ---------------------------------------------------------------------------
# Project reads (board pointers are DERIVED from project_tasks, never stored)
# ---------------------------------------------------------------------------

def _annotate_derived_state(session, projects):
    """Fill the derived board pointers on a list of project dicts, in place.

    The projects table stores no current stage/task/owner/status: they are a
    pure function of the active task rows. This is the ONE implementation of
    that derivation, shared by get_projects (board) and get_project (detail):

    - current task  = first active task with status != 'Approved' in the
      pipeline's applicable stages, ordered by sequence_no. Its stage_group,
      assigned_to and priority become current_stage / current_owner /
      current_task_priority; overall_status = 'In Progress'.
    - no open task  = 'Completed', anchored on the LAST applicable active task
      (falling back to the last applicable PIPELINE_TEMPLATES entry when no
      active rows survive: "Approval to Stake" for a prospect, "PDA" for a BP
      well); current_owner is NULL.
    - current_stage_started_at = MIN(actual_start) of the tasks in the derived
      current stage, falling back to the project's start_date.

    One batched query for the whole list, so the board never multiplies rows
    (legacy duplicate task rows collapse into the per-project grouping).
    """
    projects = [p for p in projects if p]
    if not projects:
        return
    task_rows = db.fetch_all(session, """
        SELECT project_id, task_name, stage_group, assigned_to, status, priority, actual_start
        FROM project_tasks
        WHERE project_id IN :project_ids AND is_active = 1
        ORDER BY project_id, sequence_no
    """, {"project_ids": [p["project_id"] for p in projects]})
    by_project: Dict[int, List[Dict[str, Any]]] = {}
    for row in task_rows:
        by_project.setdefault(row["project_id"], []).append(row)

    for project in projects:
        rows = by_project.get(project["project_id"], [])
        stages = applicable_stages(project.get("pipeline_type"))
        open_task = next((r for r in rows if r["stage_group"] in stages and r["status"] != "Approved"), None)
        if open_task:
            anchor = open_task
            current_owner = open_task["assigned_to"]
            overall_status = "In Progress"
        else:
            # Completed: anchor on the final applicable step of the project's
            # OWN pipeline. Prefer the last active applicable row; derive from
            # the templates when none survive, so this stays correct if a
            # later workstream removes/renumbers a step.
            fallback = next((t for t in reversed(PIPELINE_TEMPLATES) if t[2] in stages), None)
            anchor = next((r for r in reversed(rows) if r["stage_group"] in stages), None) \
                or {"task_name": fallback[1] if fallback else None,
                    "stage_group": fallback[2] if fallback else stages[-1],
                    "priority": "Medium"}
            current_owner = None
            overall_status = "Completed"
        current_stage = anchor["stage_group"]
        started = [r["actual_start"] for r in rows
                   if r["stage_group"] == current_stage and r["actual_start"]]
        project["current_stage"] = current_stage
        project["current_task"] = anchor["task_name"]
        project["current_owner"] = current_owner
        project["overall_status"] = overall_status
        project["current_stage_started_at"] = min(started) if started else project.get("start_date")
        project["current_task_priority"] = anchor.get("priority") or "Medium"


def get_projects(session, search_text="", stage_filter="All", status_filter="All",
                 owner_filter="All", health_filter="All", sort_key="Well Name", pipeline_filter="All"):
    """Return the (filtered, sorted) project board rows with derived state.

    Search/pipeline/archived filters act on stored columns and stay in SQL; the
    stage/status/owner/health filters act on DERIVED values (see
    _annotate_derived_state) and are applied in Python after annotation.

    The active_drilling subquery aggregates per project (one row each), so a
    project with multiple Quicklook task rows carrying the field appears exactly
    once, flagged active if ANY of them is truthy.
    """
    conditions = ["COALESCE(p.archived, 0) = 0"]
    params: Dict[str, Any] = {}
    needle = (search_text or "").strip().lower()
    if needle:
        conditions.append("LOWER(COALESCE(p.project_name, '')) LIKE :search_text")
        params["search_text"] = f"%{needle}%"
    if pipeline_filter in {"prospect", "bp"}:
        conditions.append("LOWER(COALESCE(p.pipeline_type, 'prospect')) = :pipeline_filter")
        params["pipeline_filter"] = pipeline_filter
    where_clause = " AND ".join(conditions)
    rows = db.fetch_all(session, f"""
        SELECT p.*,
               COALESCE(priority_flags.has_high_priority_tasks, 0) AS has_high_priority_tasks,
               COALESCE(active_drilling.is_drilling, 0) AS is_drilling
        FROM projects p
        LEFT JOIN (
            SELECT project_id,
                   MAX(CASE WHEN priority = 'High' THEN 1 ELSE 0 END) AS has_high_priority_tasks
            FROM project_tasks
            WHERE is_active = 1
            GROUP BY project_id
        ) priority_flags ON priority_flags.project_id = p.project_id
        LEFT JOIN (
            -- Aggregated per project so multiple Quicklook rows (legacy +
            -- canonical) never multiply the outer projects row.
            SELECT pt.project_id,
                   MAX(CASE WHEN LOWER(COALESCE(tdf.field_value, '')) IN ('1', 'true', 'yes', 'on')
                            THEN 1 ELSE 0 END) AS is_drilling
            FROM project_tasks pt
            JOIN task_dynamic_fields tdf ON tdf.task_id = pt.task_id
            WHERE pt.task_name IN ('Quicklook Logs Interpretation', 'Quicklook Logs')
              AND tdf.field_key = 'active_drilling'
            GROUP BY pt.project_id
        ) active_drilling ON active_drilling.project_id = p.project_id
        WHERE {where_clause}
        ORDER BY p.project_id DESC
    """, params)
    _annotate_derived_state(session, rows)
    filtered = []
    for item in rows:
        # Drilling is only surfaced while the project sits in Post-Drilling.
        item["active_drilling"] = 1 if (item.get("current_stage") == "Post-Drilling"
                                        and int(item.pop("is_drilling") or 0) == 1) else 0
        item["active_well_enabled"] = int(item.get("active_well_enabled") or 0)
        item["health"] = health_from_target(item.get("target_date"), item.get("overall_status"))
        # A fully-matured lead (every prospect step Approved) leaves the lead
        # board and lives in the Portfolio until a supervisor promotes it.
        if pipeline_filter == "prospect" and item.get("overall_status") == "Completed":
            continue
        if stage_filter != "All" and item.get("current_stage") != stage_filter:
            continue
        if status_filter != "All" and item.get("overall_status") != status_filter:
            continue
        if owner_filter != "All" and item.get("current_owner") != owner_filter:
            continue
        if health_filter != "All" and item["health"] != health_filter:
            continue
        filtered.append(item)

    def sort_fn(item):
        if sort_key == "Well Name":
            return (item.get("project_name") or "").lower()
        if sort_key == "Date Created":
            return -(item.get("project_id") or 0)
        if sort_key == "Stage":
            return STAGE_ORDER.index(item["current_stage"]) if item.get("current_stage") in STAGE_ORDER else 999
        if sort_key == "Assignee":
            return (item.get("current_owner") or "").lower()
        if sort_key == "Health":
            return {"Overdue": 0, "Due Soon": 1, "On Track": 2, "Completed": 3}.get(item["health"], 99)
        return parse_iso_date(item.get("target_date")) or date.max
    filtered.sort(key=sort_fn)
    return filtered


def get_project(session, project_id):
    """Return one project dict with derived board pointers, or None."""
    project = db.fetch_one(session, "SELECT * FROM projects WHERE project_id = :project_id",
                           {"project_id": project_id})
    if not project:
        return None
    if not project.get("lead_folder_path"):
        project["lead_folder_path"] = folders.default_lead_folder_path(project.get("project_name") or "")
    _annotate_derived_state(session, [project])
    return project


def project_completion_percent(session, project_id):
    """Percent of the current pipeline's applicable tasks that are done.

    Scoped to the stages of the project's operating pipeline (Prospect
    Maturation stages for prospects, BP Execution stages for BP wells) so the
    figure agrees with the derived overall_status (_annotate_derived_state): a
    prospect that has approved every Prospect-stage task reads 100% even though
    its BP-stage tasks are untouched. The stage filter IS the scope: every task
    in the operating pipeline counts toward the denominator.
    """
    project = get_project(session, project_id) or {}
    stages = applicable_stages(project.get("pipeline_type"))
    row = db.fetch_one(session, """
        SELECT
            COUNT(*) AS applicable_total,
            SUM(CASE WHEN status = 'Approved' THEN 1 ELSE 0 END) AS done
        FROM project_tasks
        WHERE project_id = :project_id AND is_active = 1 AND stage_group IN :stages
    """, {"project_id": project_id, "stages": stages})
    total = int(row["applicable_total"] or 0)
    done = int(row["done"] or 0)
    return round((done / total) * 100, 1) if total else 0.0


def _sync_completed_at(session, project_id):
    """Keep projects.completed_at consistent with the derived completion state.

    ``completed_at`` records when the applicable task set FIRST became fully
    approved -- history, not current state, so it is the one completion fact
    that stays stored. Rule: stamp utc_now when a write leaves the applicable
    set fully approved and the stamp is empty; clear it when a write reopens
    the set. Called from every write that can change completeness: save_task /
    transition_task (status changes) and promotion/demotion (the applicable set
    itself changes). No commit -- runs in the caller's transaction.
    """
    project = db.fetch_one(session,
                           "SELECT pipeline_type, completed_at FROM projects WHERE project_id = :project_id",
                           {"project_id": project_id})
    if not project:
        return
    stages = applicable_stages(project.get("pipeline_type"))
    open_count = db.fetch_one(session, """
        SELECT COUNT(*) AS c FROM project_tasks
        WHERE project_id = :project_id AND is_active = 1 AND stage_group IN :stages
          AND status != 'Approved'
    """, {"project_id": project_id, "stages": stages})["c"]
    if open_count == 0 and not project.get("completed_at"):
        db.execute(session,
                   "UPDATE projects SET completed_at = :now WHERE project_id = :project_id",
                   {"now": utc_now_str(), "project_id": project_id})
    elif open_count > 0 and project.get("completed_at"):
        db.execute(session,
                   "UPDATE projects SET completed_at = NULL WHERE project_id = :project_id",
                   {"project_id": project_id})


def update_project_name(session, project_id, new_name, changed_by="Admin", lead_x=None, lead_y=None):
    """Rename a project, realign default folders, and log the rename event.

    Only the name and lead coordinates are writable here. Promotion state
    (business_plan_enabled / business_plan_year / pipeline_type) and
    active_well_enabled are owned exclusively by update_project_flags
    (workflow/promotion.py), keeping pipeline_type <-> business_plan_enabled
    in lockstep.
    """
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("Lead / well name is required.")
    if len(new_name) > 120:
        raise ValueError("Lead / well name must be 120 characters or less.")
    old = get_project(session, project_id)
    if not old:
        raise ValueError("Lead / well not found.")
    updates: Dict[str, Any] = {"project_name": new_name, "last_updated": utc_now_str()}
    old_default_folder = folders.default_lead_folder_path(old.get("project_name") or "")
    if not old.get("lead_folder_path") or old.get("lead_folder_path") == old_default_folder:
        updates["lead_folder_path"] = folders.default_lead_folder_path(new_name)
    if lead_x is not None:
        updates["lead_x"] = lead_x or None
    if lead_y is not None:
        updates["lead_y"] = lead_y or None
    # Column names come from the fixed allowlisted keys above (never user input);
    # only the values are bound parameters.
    assignments = ", ".join([f"{k} = :{k}" for k in updates])
    with db.write_transaction(session):
        db.execute(session, f"UPDATE projects SET {assignments} WHERE project_id = :project_id",
                   dict(updates, project_id=project_id))
        # Keep the optional mounted server folder aligned when it is available.
        # UNC links are always regenerated from the current record name.
        try:
            old_field, old_well = folders.parse_field_and_well(old.get("project_name") or "")
            new_field, new_well = folders.parse_field_and_well(new_name)
            for root in (config.WELL_OVERVIEW_DIRECTORY_ROOT, config.LEAD_WORKFLOW_DIRECTORY_ROOT):
                old_path = root / old_field / old_well
                new_path = root / new_field / new_well
                if old_path.exists() and not new_path.exists():
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    old_path.rename(new_path)
        except Exception:
            # Folder links must not prevent a record rename when the share is not mounted.
            pass
        first_task = db.fetch_one(session,
                                  "SELECT task_id, task_name FROM project_tasks WHERE project_id = :project_id ORDER BY sequence_no LIMIT 1",
                                  {"project_id": project_id})
        if first_task:
            record_type = "Well" if str((old or {}).get("pipeline_type") or "prospect").lower() == "bp" else "Lead"
            log_task_event(session, first_task["task_id"], project_id, first_task["task_name"], f"{record_type} Renamed",
                           old.get("project_name") if old else None, new_name, changed_by, f"Renamed {record_type.lower()} to {new_name}")


def archive_project(session, project_id, changed_by="Admin", *args, **kwargs):
    """Soft-archive a project (recoverable); log the archive event."""
    project = get_project(session, project_id)
    if not project:
        raise ValueError("Lead / well not found.")
    if int(project.get("archived") or 0):
        return
    with db.write_transaction(session):
        db.execute(session,
                   "UPDATE projects SET archived = 1, last_updated = :now, revision = COALESCE(revision, 0) + 1 WHERE project_id = :project_id",
                   {"now": utc_now_str(), "project_id": project_id})
        first_task = db.fetch_one(session,
                                  "SELECT task_id, task_name FROM project_tasks WHERE project_id = :project_id ORDER BY sequence_no LIMIT 1",
                                  {"project_id": project_id})
        if first_task:
            log_task_event(session, first_task["task_id"], project_id, first_task["task_name"], "Well Archived", None, "Archived",
                           changed_by, f"Archived well: {project.get('project_name') or project_id}")


def restore_project(session, project_id, changed_by="Admin"):
    """Restore a previously archived project; log the restore event."""
    project = get_project(session, project_id)
    if not project:
        raise ValueError("Lead / well not found.")
    if not int(project.get("archived") or 0):
        return
    with db.write_transaction(session):
        db.execute(session,
                   "UPDATE projects SET archived = 0, last_updated = :now, revision = COALESCE(revision, 0) + 1 WHERE project_id = :project_id",
                   {"now": utc_now_str(), "project_id": project_id})
        first_task = db.fetch_one(session,
                                  "SELECT task_id, task_name FROM project_tasks WHERE project_id = :project_id ORDER BY sequence_no LIMIT 1",
                                  {"project_id": project_id})
        if first_task:
            log_task_event(session, first_task["task_id"], project_id, first_task["task_name"], "Well Restored", "Archived", "Active",
                           changed_by, f"Restored well: {project.get('project_name') or project_id}")


def delete_project(session, project_id, changed_by="Admin"):
    """Permanent deletion, reserved for controlled maintenance (web routes archive)."""
    with db.write_transaction(session):
        db.execute(session, "DELETE FROM projects WHERE project_id = :project_id",
                   {"project_id": project_id})
