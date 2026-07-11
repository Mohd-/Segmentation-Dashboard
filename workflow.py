"""Project and task lifecycle domain logic -- the heart of the application.

What belongs here:
- The workflow domain constants (statuses, stage ordering, the
  ``PIPELINE_TEMPLATES`` step list -- the single source of truth for the
  31-step workflow -- and the dynamic-field -> overview mirror map).
- Every project/task create/read/update operation ported from the old
  ``Database`` class: project CRUD, task saves, BP promotion/demotion,
  lead-summary snapshots, presence-CoS recalculation, history logging, overview
  and business-plan-commitment access.

What does NOT belong here:
- Pure CoS math (cos.py), report/aggregate SQL (reporting.py), Excel
  (export_excel.py), folder links (folders.py), engine/session (db.py).

Conventions:
- Every public function takes a SQLAlchemy ``session`` as its first argument; no
  hidden globals.
- Writes use ``db.write_transaction`` (upfront write lock, commit on success /
  rollback on error); reads use ``db.fetch_one`` / ``db.fetch_all``. All SQL is
  ``text()`` with named ``:param`` binds (dialect-portable). Rows come back as
  plain dicts.
- Behavior is preserved exactly, including the optimistic-locking revision bumps
  and the actual_start/actual_finish date rules.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict, List, Optional

import config
import cos
import db
import folders
from sqlalchemy.exc import IntegrityError

from helpers import health_from_target, parse_iso_date, today_str, utc_now_str


class StaleRevisionError(RuntimeError):
    """Raised when an optimistic-lock revision check fails on a task save.

    The caller supplied a ``revision`` that no longer matches the stored row
    (someone else saved first). main.py maps this to HTTP 409 with the message
    intact; it is the ONLY exception type that becomes a 409. Other
    RuntimeErrors are treated as internal errors (500).
    """


# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

# The 4-state implicit lifecycle (v17): Not Assigned -> In Progress (via
# assignment) -> Ready (submit) -> Approved (supervisor). "Not Applicable" is
# INTERNAL-only: seeding/promotion/pipeline-scoping still use it, but it is
# never offered or accepted through the API (save_task rejects it).
STATUSES = [
    "Not Assigned",
    "In Progress",
    "Ready",
    "Approved",
    "Not Applicable",
]

DONE_STATUSES = {"Approved", "Not Applicable", "Complete"}
ACTIVE_STATUSES = {"In Progress", "Ready"}

STAGE_ORDER = [
    "Lead Identification",
    "Risking",
    "Segmentation",
    "Pre-Well Delivery",
    "Well Delivery",
    "Post-Drilling",
    "Post-Testing",
]

PROSPECT_STAGES = ["Lead Identification", "Risking", "Segmentation", "Pre-Well Delivery"]
BP_EXECUTION_STAGES = ["Well Delivery", "Post-Drilling", "Post-Testing"]
BOARD_STAGE_ORDER = STAGE_ORDER[:]

# Well-level formation interpretation (project_formations). Fixed formation
# list -- users never create formations. Rows are keyed by
# (project, formation, phase); ``phase`` says which interpretation step the
# values came from.
FORMATIONS = ["SARH", "QASM", "QWRH"]
FORMATION_PHASES = ["quicklook", "final"]
FORMATION_VALUE_FIELDS = [
    "top_tvdss_ft", "base_tvdss_ft", "thickness_ft", "porosity_pct",
    "swt_pct", "pay_ft", "ngr_pct", "fluid",
]
# All value fields except 'fluid' are REAL columns (project_formations); 'fluid'
# is a free-text description and stays TEXT.
FORMATION_NUMERIC_FIELDS = [f for f in FORMATION_VALUE_FIELDS if f != "fluid"]

# The 31-step pipeline definition: (sequence_no, task_name, stage_group).
# This list is the SINGLE SOURCE OF TRUTH for the workflow -- there is no
# task_templates table; project creation materializes project_tasks rows
# straight from these tuples.
#
# Pre-deployment, editing this list only affects NEW projects (existing dev
# databases are throwaway -- delete the .db and restart; see migrations.py).
# POST-deployment, changing it requires a numbered data migration for existing
# project_tasks rows: resequencing by task_name, and deactivating retired
# steps (is_active = 0) so their inputs and audit trail survive.
PIPELINE_TEMPLATES = [
    (1, "Reservoir Area Definition", "Lead Identification"),
    (2, "Thickness Estimation", "Lead Identification"),
    (3, "Lead Resource Assessment", "Lead Identification"),
    (4, "Seismic Signature Validation", "Risking"),
    (5, "Reservoir CoS", "Risking"),
    (6, "Trap CoS", "Risking"),
    (7, "Seal CoS", "Risking"),
    # v18: "Presence CoS Evaluation" (formerly step 8) was removed as a visible
    # step -- its value is derived (Reservoir x Trap x Seal), computed by
    # recalculate_presence_cos and surfaced as project_overview.derisking.
    # The remaining steps renumber to a clean 1-31.
    (8, "Prospect Evaluation Presentation", "Segmentation"),
    (9, "Well Creation", "Pre-Well Delivery"),
    (10, "Pre-Drilling Resource Assessment", "Pre-Well Delivery"),
    (11, "Staking Moving Tolerance", "Pre-Well Delivery"),
    (12, "Approval to Stake", "Pre-Well Delivery"),
    (13, "BP Execution Gate", "Well Delivery"),
    (14, "Well Proposal", "Well Delivery"),
    (15, "Site Preparation", "Well Delivery"),
    (16, "Approval To Drill", "Well Delivery"),
    (17, "GHEER", "Well Delivery"),
    (18, "Quicklook Logs Interpretation", "Post-Drilling"),
    (19, "Aramco Picks", "Post-Drilling"),
    (20, "Post-Drilling Resource Assessment", "Post-Drilling"),
    (21, "SAD Model", "Post-Drilling"),
    (22, "Executive Summary", "Post-Drilling"),
    (23, "URED Update", "Post-Drilling"),
    (24, "Post-Well Outcome & Decision Gate", "Post-Drilling"),
    (25, "Flowback Results", "Post-Testing"),
    (26, "SAD Update", "Post-Testing"),
    (27, "Executive Summary Final", "Post-Testing"),
    (28, "Final Log Analysis", "Post-Testing"),
    (29, "PVAD Structural MTR", "Post-Testing"),
    (30, "Resource Assessment Update", "Post-Testing"),
    (31, "PDA", "Post-Testing"),
]

DYNAMIC_FIELD_OVERVIEW_MAP = {
    "lead_piip_gas_mean": "lead_ogip",
    "pre_drill_piip_gas_mean": "pre_drill_estimation",
    "post_drill_piip_gas_mean": "post_drill_estimation",
    "resource_update_gas_mean": "post_drill_estimation",
    # v18: no "presence_cos" entry -- recalculate_presence_cos writes
    # project_overview.derisking directly (the Presence step was removed).
    "quicklook_pay_thickness_ft": "quick_look_pay",
    "quicklook_average_porosity_pct": "quick_look_porosity",
    "quicklook_average_swt_pct": "quick_look_swt",
    "flowback_gas_rate_mmscfd": "flowback_results",
    # WS7: the Classification entered in the GHEER step feeds the Portfolio.
    "gheer_classification": "classification",
}

_OVERVIEW_ALLOWED_FIELDS = {
    "derisking", "ogip", "lead_ogip", "preliminary_resource_estimation", "pre_drill_estimation",
    "post_drill_estimation", "reservoir_pressure", "reservoir_gradient",
    "flowback_results", "pay", "porosity", "swt",
    "quick_look_pay", "quick_look_porosity", "quick_look_swt",
    "classification",
}


# ---------------------------------------------------------------------------
# Users (login identities + roles; seeded from config.SEED_USERS)
# ---------------------------------------------------------------------------

def get_active_users(session) -> List[Dict[str, Any]]:
    """Active users as [{name, role}] ordered by name (assignee/login dropdowns)."""
    return db.fetch_all(session,
                        "SELECT name, role FROM users WHERE is_active = 1 ORDER BY name")


def find_active_user(session, name: str) -> Optional[Dict[str, Any]]:
    """Look up an active user by name, case-insensitively.

    Returns the full row (canonical-cased ``name`` plus ``role``) or None.
    Used by login so users can type their name in any casing but the session
    always stores the canonical spelling from the users table.
    """
    return db.fetch_one(session, """
        SELECT * FROM users
        WHERE LOWER(name) = LOWER(:name) AND is_active = 1
    """, {"name": str(name or "").strip()})


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

    # The workflow definition lives in code (PIPELINE_TEMPLATES); the project
    # anchors on its pipeline's first step.
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
    first_sequence, first_task_name, first_stage = first_template
    with db.write_transaction(session):
        result = db.execute(session, """
            INSERT INTO projects (
                project_name, overall_status, current_stage, current_task, current_owner,
                start_date, target_date, current_stage_started_at, last_updated,
                lead_folder_path, lead_x, lead_y, business_plan_enabled, business_plan_year,
                active_well_enabled, pipeline_type
            ) VALUES (:project_name, :overall_status, :current_stage, :current_task, :current_owner,
                      :start_date, :target_date, :stage_started_at, :last_updated,
                      :lead_folder_path, :lead_x, :lead_y, :business_plan_enabled, :business_plan_year,
                      :active_well_enabled, :pipeline_type)
        """, {
            "project_name": project_name, "overall_status": "In Progress",
            "current_stage": first_stage, "current_task": first_task_name,
            "current_owner": None, "start_date": start_date,
            "target_date": target_date, "stage_started_at": start_date, "last_updated": now,
            "lead_folder_path": folders.default_lead_folder_path(project_name),
            "lead_x": lead_x or None, "lead_y": lead_y or None,
            "business_plan_enabled": bp_enabled, "business_plan_year": year_val,
            "active_well_enabled": 1 if active_well_enabled else 0, "pipeline_type": pipeline_type,
        })
        project_id = result.lastrowid  # PG: use RETURNING when on Postgres
        first_task_id = None
        for sequence_no, task_name, stage_group in PIPELINE_TEMPLATES:
            is_bp_stage = stage_group in BP_EXECUTION_STAGES
            if pipeline_type == "bp" and not is_bp_stage:
                initial_status = "Not Applicable"
            else:
                # v17 lifecycle: every step starts Not Assigned; assignment
                # moves it to In Progress.
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

        db.execute(session,
                   "INSERT OR IGNORE INTO project_overview (project_id, last_updated) VALUES (:project_id, :now)",
                   {"project_id": project_id, "now": now})
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
        refresh_project_state(session, project_id)
    return project_id


# ---------------------------------------------------------------------------
# Project reads
# ---------------------------------------------------------------------------

def get_projects(session, search_text="", stage_filter="All", status_filter="All",
                 owner_filter="All", health_filter="All", sort_key="Well Name", pipeline_filter="All"):
    """Return the (filtered, sorted) project board rows with derived health.

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
    if stage_filter != "All":
        conditions.append("p.current_stage = :stage_filter")
        params["stage_filter"] = stage_filter
    if status_filter != "All":
        conditions.append("p.overall_status = :status_filter")
        params["status_filter"] = status_filter
    if owner_filter != "All":
        conditions.append("p.current_owner = :owner_filter")
        params["owner_filter"] = owner_filter
    if pipeline_filter in {"prospect", "bp"}:
        conditions.append("LOWER(COALESCE(p.pipeline_type, 'prospect')) = :pipeline_filter")
        params["pipeline_filter"] = pipeline_filter
    where_clause = " AND ".join(conditions)
    rows = db.fetch_all(session, f"""
        SELECT p.*,
               COALESCE(pt_current.priority, 'Medium') AS current_task_priority,
               COALESCE(priority_flags.has_high_priority_tasks, 0) AS has_high_priority_tasks,
               CASE WHEN p.current_stage = 'Post-Drilling'
                         AND COALESCE(active_drilling.is_drilling, 0) = 1
                    THEN 1 ELSE 0 END AS active_drilling
        FROM projects p
        LEFT JOIN project_tasks pt_current
          ON pt_current.project_id = p.project_id
         AND pt_current.task_name = p.current_task
         AND pt_current.is_active = 1
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
    filtered = []
    for item in rows:
        item["active_well_enabled"] = int(item.get("active_well_enabled") or 0)
        item["health"] = health_from_target(item.get("target_date"), item.get("overall_status"))
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
    """Return one project dict (with a default lead-folder path), or None."""
    project = db.fetch_one(session, "SELECT * FROM projects WHERE project_id = :project_id",
                           {"project_id": project_id})
    if not project:
        return None
    if not project.get("lead_folder_path"):
        project["lead_folder_path"] = folders.default_lead_folder_path(project.get("project_name") or "")
    return project


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


def project_completion_percent(session, project_id):
    """Percent of the current pipeline's applicable tasks that are done.

    Scoped to the stages of the project's operating pipeline (Prospect
    Maturation stages for prospects, BP Execution stages for BP wells) so the
    figure agrees with the overall_status logic in refresh_project_state: a
    prospect that has approved every Prospect-stage task reads 100% even though
    its BP-stage tasks are untouched. Not Applicable tasks never count.
    """
    project = get_project(session, project_id) or {}
    stages = BP_EXECUTION_STAGES if str(project.get("pipeline_type") or "prospect").lower() == "bp" else PROSPECT_STAGES
    row = db.fetch_one(session, """
        SELECT
            SUM(CASE WHEN status != 'Not Applicable' THEN 1 ELSE 0 END) AS applicable_total,
            SUM(CASE WHEN status IN ('Approved', 'Complete') THEN 1 ELSE 0 END) AS done
        FROM project_tasks
        WHERE project_id = :project_id AND is_active = 1 AND stage_group IN :stages
    """, {"project_id": project_id, "stages": stages})
    total = int(row["applicable_total"] or 0)
    done = int(row["done"] or 0)
    return round((done / total) * 100, 1) if total else 0.0


def update_project_name(session, project_id, new_name, changed_by="Admin", lead_x=None, lead_y=None,
                        business_plan_year=None, business_plan_enabled=None, active_well_enabled=None):
    """Rename a project, realign default folders, and log the rename event."""
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
    if business_plan_enabled is not None:
        updates["business_plan_enabled"] = 1 if business_plan_enabled else 0
    if active_well_enabled is not None:
        updates["active_well_enabled"] = 1 if active_well_enabled else 0
    if business_plan_year is not None and str(business_plan_year).strip():
        y = int(business_plan_year)
        if y < 2026 or y > 2040:
            raise ValueError("Select a business plan year from 2026 to 2040.")
        updates["business_plan_year"] = y
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


def reconcile_project_flow(session, project_id):
    """Return the current open task for a project's applicable pipeline stages.

    If no task is explicitly open, continue from the current task position rather
    than jumping back to the first incomplete item. Enforces no dependencies or
    locks -- it only determines which task the UI should treat as current.
    """
    project = get_project(session, project_id) or {}
    pipeline_type = str(project.get("pipeline_type") or "prospect").lower()
    applicable_stages = BP_EXECUTION_STAGES if pipeline_type == "bp" else PROSPECT_STAGES

    # ``IN :stages`` with a list value becomes an expanding bindparam (see
    # db._prepare): SQLAlchemy renders one placeholder per element at execution
    # time, portably across dialects. Never build "?,?,?" strings by hand.
    row = db.fetch_one(session, """
        SELECT * FROM project_tasks
        WHERE project_id = :project_id AND is_active = 1 AND status IN ('In Progress','Ready')
          AND stage_group IN :stages
        ORDER BY sequence_no
        LIMIT 1
    """, {"project_id": project_id, "stages": applicable_stages})
    if row:
        return row

    current_task = project.get("current_task")
    current_seq = 0
    if current_task:
        seq_row = db.fetch_one(session, """
            SELECT sequence_no FROM project_tasks
            WHERE project_id = :project_id AND task_name = :task_name AND is_active = 1
            LIMIT 1
        """, {"project_id": project_id, "task_name": current_task})
        current_seq = seq_row["sequence_no"] if seq_row else 0

    # >= (not >): with the v17 lifecycle a project can sit entirely in
    # "Not Assigned" (no active rows at all), and the current task itself is
    # then still the open one -- skipping it would wrongly anchor on the NEXT
    # step. A done current task is excluded by the status predicate anyway, so
    # advancement past completed steps is unchanged.
    row = db.fetch_one(session, """
        SELECT * FROM project_tasks
        WHERE project_id = :project_id AND is_active = 1 AND status NOT IN ('Approved','Not Applicable','Complete') AND sequence_no >= :current_seq
          AND stage_group IN :stages
        ORDER BY sequence_no
        LIMIT 1
    """, {"project_id": project_id, "current_seq": current_seq, "stages": applicable_stages})
    if row:
        return row

    row = db.fetch_one(session, """
        SELECT * FROM project_tasks
        WHERE project_id = :project_id AND is_active = 1 AND status NOT IN ('Approved','Not Applicable','Complete')
          AND stage_group IN :stages
        ORDER BY sequence_no
        LIMIT 1
    """, {"project_id": project_id, "stages": applicable_stages})
    return row


# ---------------------------------------------------------------------------
# Project overview
# ---------------------------------------------------------------------------

def update_project_overview_fields(session, project_id, fields):
    """Upsert allowed overview fields for a project (no commit -- caller commits)."""
    clean = {k: (v.strip() if isinstance(v, str) else v) for k, v in (fields or {}).items() if k in _OVERVIEW_ALLOWED_FIELDS}
    if not clean:
        return
    db.execute(session,
               "INSERT OR IGNORE INTO project_overview (project_id, last_updated) VALUES (:project_id, :now)",
               {"project_id": project_id, "now": utc_now_str()})
    # Column names come from the _OVERVIEW_ALLOWED_FIELDS allowlist; values are binds.
    assignments = ", ".join([f"{k} = :{k}" for k in clean]) + ", last_updated = :now"
    db.execute(session, f"UPDATE project_overview SET {assignments} WHERE project_id = :project_id",
               dict(clean, now=utc_now_str(), project_id=project_id))


def get_project_overview(session, project_id: int):
    """Return the project_overview row as a dict (pure read; {} if missing).

    Every project is guaranteed an overview row since the v16 backfill, so the
    empty-dict fallback only guards freshly-deleted edge cases.
    """
    return db.fetch_one(session,
                        "SELECT * FROM project_overview WHERE project_id = :project_id",
                        {"project_id": project_id}) or {}


def get_project_dynamic_field_map(session, project_id: int):
    """Return {task_name: {field_key: value}} for a project's active tasks."""
    rows = db.fetch_all(session, """
        SELECT pt.task_name, pt.sequence_no, tdf.field_key, tdf.field_value
        FROM project_tasks pt
        LEFT JOIN task_dynamic_fields tdf ON tdf.task_id = pt.task_id
        WHERE pt.project_id = :project_id AND pt.is_active = 1
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
# Well-level formation data (project_formations)
# ---------------------------------------------------------------------------

def get_project_formations(session, project_id: int):
    """Return all formation rows for a project, ordered by phase then the
    canonical formation order (SARH, QASM, QWRH)."""
    rows = db.fetch_all(session, """
        SELECT * FROM project_formations
        WHERE project_id = :project_id
    """, {"project_id": project_id})
    order = {name: index for index, name in enumerate(FORMATIONS)}
    rows.sort(key=lambda r: (r["phase"], order.get(r["formation"], 99)))
    return rows


def upsert_project_formations(session, project_id, phase, rows, changed_by="Web User", source_task_id=None):
    """Upsert formation rows for one phase; return the fresh full list.

    Each row is a full replacement for its (project_id, formation, phase) slot:
    absent numeric fields are stored as NULL, absent ``fluid`` as ''. Validation
    is strict -- an unknown phase, formation or field key raises ValueError
    (-> 400) rather than being silently dropped, so client typos never lose
    data quietly. Numeric fields that don't parse as a float also raise
    ValueError (-> 400) naming the offending field, so junk input never lands
    silently as NULL.

    When ``source_task_id`` is provided, ONE "Formation Data Updated" history
    event is logged against that task listing the formations touched. No role
    gate here: step-level assignment governs who edits. No commit -- runs in
    its own write transaction like the other mutators.
    """
    phase = str(phase or "").strip().lower()
    if phase not in FORMATION_PHASES:
        raise ValueError("Unknown phase. Use one of: " + ", ".join(FORMATION_PHASES) + ".")
    rows = rows or []
    if not isinstance(rows, list):
        raise ValueError("rows must be a list of formation objects.")

    clean_rows = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each formation row must be an object.")
        formation = str(row.get("formation") or "").strip().upper()
        if formation not in FORMATIONS:
            raise ValueError("Unknown formation. Use one of: " + ", ".join(FORMATIONS) + ".")
        unknown = [k for k in row if k not in FORMATION_VALUE_FIELDS and k != "formation"]
        if unknown:
            raise ValueError("Unknown formation fields: " + ", ".join(sorted(unknown)) + ".")
        values = {"fluid": "" if row.get("fluid") is None else str(row.get("fluid")).strip()}
        for field in FORMATION_NUMERIC_FIELDS:
            raw = row.get(field)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                values[field] = None
                continue
            try:
                values[field] = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"Invalid numeric value for {field}: {raw!r}.")
        clean_rows.append((formation, values))

    with db.write_transaction(session):
        project = get_project(session, project_id)
        if not project:
            raise ValueError("Lead / well not found.")
        now = utc_now_str()
        for formation, values in clean_rows:
            params = {"project_id": project_id, "formation": formation, "phase": phase,
                      "source_task_id": source_task_id, "now": now, "changed_by": changed_by}
            params.update(values)
            db.execute(session, """
                INSERT INTO project_formations (
                    project_id, formation, phase, top_tvdss_ft, base_tvdss_ft, thickness_ft,
                    porosity_pct, swt_pct, pay_ft, ngr_pct, fluid, source_task_id, updated_at, updated_by
                ) VALUES (:project_id, :formation, :phase, :top_tvdss_ft, :base_tvdss_ft, :thickness_ft,
                          :porosity_pct, :swt_pct, :pay_ft, :ngr_pct, :fluid, :source_task_id, :now, :changed_by)
                ON CONFLICT(project_id, formation, phase) DO UPDATE SET
                    top_tvdss_ft = excluded.top_tvdss_ft,
                    base_tvdss_ft = excluded.base_tvdss_ft,
                    thickness_ft = excluded.thickness_ft,
                    porosity_pct = excluded.porosity_pct,
                    swt_pct = excluded.swt_pct,
                    pay_ft = excluded.pay_ft,
                    ngr_pct = excluded.ngr_pct,
                    fluid = excluded.fluid,
                    source_task_id = excluded.source_task_id,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
            """, params)
        if clean_rows and source_task_id is not None:
            task = get_task(session, source_task_id)
            if task:
                touched = ", ".join(formation for formation, _values in clean_rows)
                log_task_event(session, task["task_id"], project_id, task["task_name"],
                               "Formation Data Updated", None, None, changed_by,
                               f"Updated formation data ({phase}): {touched}.")
    return get_project_formations(session, project_id)


# ---------------------------------------------------------------------------
# Lead summary snapshots + BP promotion / demotion
# ---------------------------------------------------------------------------

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
    """Promote a matured lead into the BP Execution pipeline without losing its lead record."""
    project = get_project(session, project_id)
    if not project:
        raise ValueError("Lead / well not found.")
    if str(project.get("pipeline_type") or "prospect").lower() != "bp":
        _capture_lead_summary_snapshot(session, project_id, changed_by)

    bp_tasks = db.fetch_all(session, """
        SELECT * FROM project_tasks
        WHERE project_id = :project_id AND stage_group IN :stages AND is_active = 1
        ORDER BY sequence_no
    """, {"project_id": project_id, "stages": BP_EXECUTION_STAGES})
    if not bp_tasks:
        raise RuntimeError("Business Plan workflow is not available for this lead.")
    now = utc_now_str()
    for task in bp_tasks:
        old_status = task["status"] or "Not Assigned"
        # v17 lifecycle: promotion opens Not Applicable BP tasks as Not Assigned
        # (assignment is what moves a step to In Progress). Any existing BP
        # progress is left untouched.
        if old_status == "Not Applicable":
            db.execute(session, """
                UPDATE project_tasks
                SET status = 'Not Assigned', business_plan_enabled = 1, business_plan_year = :year, last_updated = :now, revision = COALESCE(revision, 0) + 1
                WHERE task_id = :task_id
            """, {"year": year_val, "now": now, "task_id": task["task_id"]})
        else:
            db.execute(session, """
                UPDATE project_tasks
                SET business_plan_enabled = 1, business_plan_year = :year, last_updated = :now
                WHERE task_id = :task_id
            """, {"year": year_val, "now": now, "task_id": task["task_id"]})

    first_open = db.fetch_one(session, """
        SELECT * FROM project_tasks
        WHERE project_id = :project_id AND stage_group IN :stages AND is_active = 1
          AND status NOT IN ('Approved','Not Applicable','Complete')
        ORDER BY sequence_no LIMIT 1
    """, {"project_id": project_id, "stages": BP_EXECUTION_STAGES})
    if not first_open:
        first_open = bp_tasks[0]
    db.execute(session, """
        UPDATE projects
        SET pipeline_type = 'bp', business_plan_enabled = 1, business_plan_year = :year,
            current_stage = :stage, current_task = :task, current_owner = :owner, current_stage_started_at = :today,
            overall_status = 'In Progress', last_updated = :now, revision = COALESCE(revision, 0) + 1
        WHERE project_id = :project_id
    """, {"year": year_val, "stage": first_open["stage_group"], "task": first_open["task_name"],
          "owner": first_open["assigned_to"], "today": today_str(), "now": now, "project_id": project_id})


def _move_bp_to_lead_phase(session, project_id: int, changed_by: str):
    """Return a promoted BP Well to Prospect Maturation without data loss."""
    project = get_project(session, project_id)
    if not project:
        raise ValueError("Lead / well not found.")
    now = utc_now_str()
    db.execute(session, """
        UPDATE projects
        SET pipeline_type = 'prospect', business_plan_enabled = 0,
            business_plan_year = NULL, last_updated = :now,
            revision = COALESCE(revision, 0) + 1
        WHERE project_id = :project_id
    """, {"now": now, "project_id": project_id})
    db.execute(session, """
        UPDATE project_tasks
        SET business_plan_enabled = 0, business_plan_year = NULL,
            last_updated = :now
        WHERE project_id = :project_id
    """, {"now": now, "project_id": project_id})
    lead_open = db.fetch_one(session, """
        SELECT * FROM project_tasks
        WHERE project_id = :project_id AND stage_group IN :stages AND is_active = 1
          AND status NOT IN ('Approved','Not Applicable','Complete')
        ORDER BY sequence_no LIMIT 1
    """, {"project_id": project_id, "stages": PROSPECT_STAGES})
    if not lead_open:
        lead_open = db.fetch_one(session, """
            SELECT * FROM project_tasks
            WHERE project_id = :project_id AND stage_group IN :stages AND is_active = 1
            ORDER BY sequence_no LIMIT 1
        """, {"project_id": project_id, "stages": PROSPECT_STAGES})
    if lead_open:
        db.execute(session, """
            UPDATE projects
            SET current_stage = :stage, current_task = :task, current_owner = :owner,
                overall_status = 'In Progress', current_stage_started_at = :today,
                last_updated = :now
            WHERE project_id = :project_id
        """, {"stage": lead_open['stage_group'], "task": lead_open['task_name'],
              "owner": lead_open['assigned_to'], "today": today_str(), "now": now,
              "project_id": project_id})


def set_business_plan(session, project_id, enabled, year=None, changed_by="Admin", *args, **kwargs):
    """Enable/disable the Business Plan for a project (promotion / demotion)."""
    old = get_project(session, project_id)
    if not old:
        raise ValueError("Lead / well not found.")
    enabled_int = 1 if enabled else 0
    year_val = None
    if enabled_int:
        year_val = int(year or old.get("business_plan_year") or 0)
        if year_val < 2026 or year_val > 2040:
            raise ValueError("Select a business plan year from 2026 to 2040.")
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
        refresh_project_state(session, project_id)


def update_project_flags(session, project_id, business_plan_enabled=None, active_well_enabled=None, business_plan_year=None, changed_by="Web User"):
    """Apply BP promotion/demotion and/or the active-well flag for a project."""
    old = get_project(session, project_id)
    if not old:
        raise ValueError("Lead / well not found.")
    # Promotion is an atomic business operation: capture lead summary, switch pipeline, activate BP tasks.
    if business_plan_enabled is not None:
        requested_year = business_plan_year if business_plan_enabled else None
        set_business_plan(session, project_id, bool(business_plan_enabled), requested_year, changed_by)
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
    """Upsert dynamic fields, mirror mapped fields to the overview, and log a note.

    Shared by :func:`save_task` and :func:`save_task_dynamic_fields` so the
    field-upsert + overview-mirror + history-note logic exists in exactly one
    place. Does not commit or touch task status/revision.
    """
    task_id = task["task_id"]
    changed_keys = []
    overview_updates = {}
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
        if key in DYNAMIC_FIELD_OVERVIEW_MAP:
            overview_updates[DYNAMIC_FIELD_OVERVIEW_MAP[key]] = val
    if overview_updates:
        update_project_overview_fields(session, task["project_id"], overview_updates)
    if changed_keys:
        readable = [key.replace("_", " ") for key in changed_keys]
        listed = ", ".join(readable[:8])
        if len(readable) > 8:
            listed += ", and more"
        log_task_event(session, task_id, task["project_id"], task["task_name"], "Component Inputs Updated",
                       None, None, changed_by, f"Updated inputs: {listed}.")


def save_task_dynamic_fields(session, task_id, fields, changed_by="Web User"):
    """Save a task's dynamic fields only (no status change, no revision check).

    Seal CoS is recomputed on save; Reservoir/Trap/Seal CoS saves trigger the
    automatic Presence CoS recalculation.
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
        if task.get("task_name") in {"Reservoir CoS", "Trap CoS", "Seal CoS"}:
            recalculate_presence_cos(session, task["project_id"], changed_by)


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
    # the user-facing STATUSES -- "Not Applicable" is internal-only and is
    # rejected at the API boundary alongside legacy names.
    status_supplied = payload.get("status") is not None
    status = str(payload.get("status") or "").strip() if status_supplied else None
    if status_supplied and (status not in STATUSES or status == "Not Applicable"):
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

        if task.get("task_name") in {"Reservoir CoS", "Trap CoS", "Seal CoS"}:
            recalculate_presence_cos(session, task["project_id"], changed_by)

        if status != old_status or assigned_to != old_assigned_to or comments != old_comments or priority != old_priority:
            log_task_event(session, task_id, task["project_id"], task["task_name"], "Component Update",
                           old_status, status, changed_by, comments or f"Status set to {status}.")

        current = get_task(session, task_id)
        if status in DONE_STATUSES:
            db.execute(session, """
                UPDATE projects
                SET current_stage = :stage, current_task = :task, current_owner = :owner,
                    last_updated = :now, revision = revision + 1
                WHERE project_id = :project_id
            """, {"stage": current["stage_group"], "task": current["task_name"],
                  "owner": current["assigned_to"], "now": now, "project_id": current["project_id"]})
        refresh_project_state(session, current["project_id"])
        db.execute(session, "UPDATE projects SET revision = revision + 1 WHERE project_id = :project_id",
                   {"project_id": current["project_id"]})
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
      Rows already In Progress / Ready / Approved / Not Applicable are never
      touched.
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
        applicable_stages = BP_EXECUTION_STAGES if str(project.get("pipeline_type") or "prospect").lower() == "bp" else PROSPECT_STAGES
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
                  "stages": applicable_stages})

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

        refresh_project_state(session, task["project_id"])
        db.execute(session, "UPDATE projects SET revision = revision + 1 WHERE project_id = :project_id",
                   {"project_id": task["project_id"]})
        result = get_task(session, task_id) or {}
    return result


# action -> (required current status, resulting status)
TASK_TRANSITIONS = {
    "submit": ("In Progress", "Ready"),
    "approve": ("Ready", "Approved"),
    "return": ("Ready", "In Progress"),
}

_TRANSITION_EVENTS = {
    "submit": "Component Submitted",
    "approve": "Component Approved",
    "return": "Component Returned",
}


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
    logged with the old/new status; project state is refreshed. Returns the
    fresh task row.
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

        refresh_project_state(session, task["project_id"])
        db.execute(session, "UPDATE projects SET revision = revision + 1 WHERE project_id = :project_id",
                   {"project_id": task["project_id"]})
        result = get_task(session, task_id) or {}
    return result


# ---------------------------------------------------------------------------
# Presence CoS recalculation + CoS field lookups
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


def last_reservoir_cos_row_value(raw_rows_json, key):
    """Return the LAST non-empty ``key`` value from a reservoir_cos_rows JSON.

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
    for row in reversed(rows):
        value = (row or {}).get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def _final_reservoir_cos_value(session, project_id):
    """Return the last completed Reservoir CoS row value (the final Reservoir CoS)."""
    raw = _task_field_value(session, project_id, "Reservoir CoS", "reservoir_cos_rows")
    return last_reservoir_cos_row_value(raw, "reservoir_cos_pct")


def recalculate_presence_cos(session, project_id, changed_by="System"):
    """Recompute the automatic Presence CoS (Total Chance of Success).

    The final Reservoir CoS is the last completed row in Reservoir CoS;
    Presence CoS always equals Reservoir x Trap x Seal. Since v18 the value has
    no workflow step of its own: it is written ONLY to
    ``project_overview.derisking`` (surfaced in the detail payload's
    ``overview``), never to task dynamic fields. On change, the history event
    is logged against the project's Seal CoS task -- the final input of the
    formula. No commit here -- the caller's transaction owns the commit.
    """
    reservoir = _final_reservoir_cos_value(session, project_id)
    trap = _task_field_value(session, project_id, "Trap CoS", "trap_cos_pct")
    seal = _task_field_value(session, project_id, "Seal CoS", "seal_cos_pct")
    values = cos.calculate_presence_cos(reservoir, trap, seal)
    new_value = str(values.get("presence_cos", "") or "")
    previous = db.fetch_one(session,
                            "SELECT derisking FROM project_overview WHERE project_id = :project_id",
                            {"project_id": project_id})
    old_value = "" if not previous or previous["derisking"] is None else str(previous["derisking"])
    update_project_overview_fields(session, project_id, {"derisking": new_value})
    if new_value != old_value:
        seal_task = db.fetch_one(session, """
            SELECT task_id FROM project_tasks
            WHERE project_id = :project_id AND task_name = 'Seal CoS' AND is_active = 1
            ORDER BY task_id DESC LIMIT 1
        """, {"project_id": project_id})
        if seal_task:
            note = "Total Chance of Success automatically recalculated from final Reservoir CoS x Trap CoS x Seal CoS."
            log_task_event(session, seal_task["task_id"], project_id, "Seal CoS",
                           "Presence CoS Calculated", None, None, changed_by, note)
    return values


# ---------------------------------------------------------------------------
# Project state refresh + history + location
# ---------------------------------------------------------------------------

def refresh_project_state(session, project_id):
    """Recompute a project's current stage/task/owner and overall status.

    Uses reconcile_project_flow to find the active task; when none remain in the
    applicable pipeline stages the project is marked Completed and anchored on
    the final (highest-sequence) task of its own pipeline -- "Approval to Stake"
    for a completed prospect, "PDA" for a completed BP well.

    ``completed_at`` bookkeeping (schema v16): stamped with the current UTC time
    exactly when overall_status TRANSITIONS to 'Completed'; cleared (NULL) when
    a completed project transitions back to In Progress; untouched otherwise.
    No commit -- runs within the caller's transaction.
    """
    active = reconcile_project_flow(session, project_id)
    # The completed_at fragments are fixed strings chosen by the transition
    # check -- never user input.
    clear_completed_sql = ", completed_at = NULL"
    stamp_completed_sql = ", completed_at = :completed_at"

    if active:
        new_stage = active["stage_group"]
        new_task = active["task_name"]
        new_owner = active["assigned_to"]
        overall_status = "In Progress"
        project = get_project(session, project_id)
        was_completed = (project or {}).get("overall_status") == "Completed"
        current_stage_started_at = project["current_stage_started_at"]
        if project["current_stage"] != new_stage:
            current_stage_started_at = today_str()
        db.execute(session, f"""
            UPDATE projects
            SET current_stage = :stage, current_task = :task, current_owner = :owner, overall_status = :overall_status,
                current_stage_started_at = :stage_started_at, last_updated = :now
                {clear_completed_sql if was_completed else ''}
            WHERE project_id = :project_id
        """, {"stage": new_stage, "task": new_task, "owner": new_owner,
              "overall_status": overall_status, "stage_started_at": current_stage_started_at,
              "now": utc_now_str(), "project_id": project_id})
    else:
        project = get_project(session, project_id) or {}
        was_completed = project.get("overall_status") == "Completed"
        applicable_stages = BP_EXECUTION_STAGES if str(project.get("pipeline_type") or "prospect").lower() == "bp" else PROSPECT_STAGES
        incomplete = db.fetch_one(session, """
            SELECT COUNT(*) AS c FROM project_tasks
            WHERE project_id = :project_id AND is_active = 1 AND stage_group IN :stages
              AND status NOT IN ('Approved','Not Applicable','Complete')
        """, {"project_id": project_id, "stages": applicable_stages})["c"]
        overall_status = "Completed" if incomplete == 0 else "In Progress"
        if overall_status == "Completed":
            newly_completed = not was_completed
            final_done = db.fetch_one(session, """
                SELECT task_name, stage_group
                FROM project_tasks
                WHERE project_id = :project_id AND is_active = 1 AND stage_group IN :stages
                ORDER BY sequence_no DESC
                LIMIT 1
            """, {"project_id": project_id, "stages": applicable_stages})
            # Fallback anchor when no active rows survive: derive from the
            # pipeline templates rather than hardcoding names. The last template
            # whose stage belongs to this pipeline is the true final step
            # ("Approval to Stake"/"Pre-Well Delivery" for prospect, "PDA"/
            # "Post-Testing" for bp). Deriving keeps this correct if a later
            # workstream removes/renumbers a step.
            fallback = next((t for t in reversed(PIPELINE_TEMPLATES) if t[2] in applicable_stages), None)
            final_task_name = final_done["task_name"] if final_done else (fallback[1] if fallback else None)
            final_stage = final_done["stage_group"] if final_done else (fallback[2] if fallback else applicable_stages[-1])
            params = {"stage": final_stage, "task": final_task_name, "overall_status": overall_status,
                      "today": today_str(), "now": utc_now_str(), "project_id": project_id}
            if newly_completed:
                params["completed_at"] = utc_now_str()
            db.execute(session, f"""
                UPDATE projects
                SET current_stage = :stage, current_task = :task, current_owner = NULL,
                    overall_status = :overall_status, current_stage_started_at = :today, last_updated = :now
                    {stamp_completed_sql if newly_completed else ''}
                WHERE project_id = :project_id
            """, params)
        else:
            earliest_open = db.fetch_one(session, """
                SELECT stage_group, task_name, assigned_to
                FROM project_tasks
                WHERE project_id = :project_id AND is_active = 1 AND stage_group IN :stages
                  AND status NOT IN ('Approved','Not Applicable','Complete')
                ORDER BY sequence_no
                LIMIT 1
            """, {"project_id": project_id, "stages": applicable_stages})
            if earliest_open:
                db.execute(session, f"""
                    UPDATE projects
                    SET current_stage = :stage, current_task = :task, current_owner = :owner,
                        overall_status = :overall_status, last_updated = :now
                        {clear_completed_sql if was_completed else ''}
                    WHERE project_id = :project_id
                """, {"stage": earliest_open['stage_group'], "task": earliest_open['task_name'],
                      "owner": earliest_open['assigned_to'], "overall_status": overall_status,
                      "now": utc_now_str(), "project_id": project_id})
            else:
                db.execute(session, f"""
                    UPDATE projects SET overall_status = :overall_status, last_updated = :now
                        {clear_completed_sql if was_completed else ''}
                    WHERE project_id = :project_id
                """, {"overall_status": overall_status, "now": utc_now_str(), "project_id": project_id})


def log_task_event(session, task_id, project_id, task_name, action_type, old_status, new_status, changed_by, comment):
    """Append one row to the task_history audit trail (no commit)."""
    db.execute(session, """
        INSERT INTO task_history (
            task_id, project_id, task_name, action_type, old_status, new_status, changed_at, changed_by, comment
        ) VALUES (:task_id, :project_id, :task_name, :action_type, :old_status, :new_status, :changed_at, :changed_by, :comment)
    """, {"task_id": task_id, "project_id": project_id, "task_name": task_name,
          "action_type": action_type, "old_status": old_status, "new_status": new_status,
          "changed_at": utc_now_str(), "changed_by": changed_by, "comment": comment})
