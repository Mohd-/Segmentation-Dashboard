"""Project and task lifecycle domain logic -- the heart of the application.

What belongs here:
- The workflow domain constants (statuses, stage ordering, the pipeline
  templates, task renames, the dynamic-field -> overview mirror map).
- Every project/task create/read/update operation ported from the old
  ``Database`` class: seeding, project CRUD, task saves, BP promotion/demotion,
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
from datetime import date, timedelta
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

STATUSES = [
    "Not Assigned",
    "Assigned",
    "In Progress",
    "Ready for Review",
    "Under Review",
    "Ready for Approval",
    "Returned for Update",
    "Approved",
    "Not Applicable",
]

DONE_STATUSES = {"Approved", "Not Applicable", "Complete"}
ACTIVE_STATUSES = {"Assigned", "In Progress", "Ready for Review", "Under Review", "Ready for Approval", "Returned for Update"}

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

# Template tuple: id, component, stage, role, duration, depends_on, branch_type, output
PIPELINE_TEMPLATES = [
    (1, "Reservoir Area Definition", "Lead Identification", "Lead Owner", 3, None, "normal", "Reservoir area defined"),
    (2, "Thickness Estimation", "Lead Identification", "Lead Owner", 3, None, "normal", "Thickness estimated"),
    (3, "Lead Resource Assessment", "Lead Identification", "Reservoir Engineer", 3, None, "normal", "Lead resources assessed"),
    (4, "Seismic Signature Validation", "Risking", "Geologist", 2, None, "normal", "Seismic signature validated"),
    (5, "Reservoir CoS", "Risking", "Reservoir Engineer", 3, None, "normal", "Reservoir CoS entered"),
    (6, "Trap CoS", "Risking", "Geologist", 3, None, "normal", "Trap CoS entered"),
    (7, "Seal CoS", "Risking", "Geologist", 3, None, "normal", "Seal CoS entered"),
    (8, "Presence CoS Evaluation", "Risking", "Geologist", 2, None, "normal", "Presence CoS entered"),
    (9, "Prospect Evaluation Presentation", "Segmentation", "Lead Owner", 2, None, "normal", "Presentation prepared"),
    (10, "Well Creation", "Pre-Well Delivery", "Well Planner", 3, None, "normal", "Well created"),
    (11, "Pre-Drilling Resource Assessment", "Pre-Well Delivery", "Reservoir Engineer", 3, None, "normal", "Pre-drilling resources assessed"),
    (12, "Staking Moving Tolerance", "Pre-Well Delivery", "Geologist", 2, None, "normal", "Moving tolerance recorded"),
    (13, "Approval to Stake", "Pre-Well Delivery", "Stakeholder", 2, None, "normal", "Approval to stake complete"),
    (14, "BP Execution Gate", "Well Delivery", "Portfolio Team", 1, None, "normal", "BP execution gate complete"),
    (15, "Well Proposal", "Well Delivery", "Drilling Engineer", 3, None, "normal", "Well proposal complete"),
    (16, "Site Preparation", "Well Delivery", "Field Team", 4, None, "normal", "Site preparation complete"),
    (17, "Approval To Drill", "Well Delivery", "Approver", 2, None, "normal", "Approval to drill complete"),
    (18, "GHEER", "Well Delivery", "HSE / Review Team", 2, None, "normal", "GHEER complete"),
    (19, "Quicklook Logs Interpretation", "Post-Drilling", "Petrophysicist", 2, None, "normal", "Quicklook logs interpreted"),
    (20, "Aramco Picks", "Post-Drilling", "Geologist", 2, None, "normal", "Aramco picks complete"),
    (21, "Post-Drilling Resource Assessment", "Post-Drilling", "Reservoir Engineer", 3, None, "normal", "Post-drilling resources assessed"),
    (22, "SAD Model", "Post-Drilling", "PDA Owner", 3, None, "normal", "SAD model complete"),
    (23, "Executive Summary", "Post-Drilling", "Manager", 2, None, "normal", "Executive summary complete"),
    (24, "URED Update", "Post-Drilling", "Reservoir Engineer", 2, None, "normal", "URED update complete"),
    (25, "Post-Well Outcome & Decision Gate", "Post-Drilling", "Portfolio Team", 3, None, "normal", "Outcome decision complete"),
    (26, "Flowback Results", "Post-Testing", "Analyst", 3, None, "normal", "Flowback results captured"),
    (27, "SAD Update", "Post-Testing", "PDA Owner", 3, None, "normal", "SAD update complete"),
    (28, "Executive Summary Final", "Post-Testing", "Manager", 2, None, "normal", "Final executive summary complete"),
    (29, "Final Log Analysis", "Post-Testing", "Petrophysicist", 2, None, "normal", "Final log analysis complete"),
    (30, "PVAD Structural MTR", "Post-Testing", "Reporting Owner", 2, None, "normal", "PVAD structural MTR complete"),
    (31, "Resource Assessment Update", "Post-Testing", "Reservoir Engineer", 2, None, "normal", "Resource assessment updated"),
    (32, "PDA", "Post-Testing", "PDA Owner", 2, None, "normal", "PDA complete"),
]

WORKFLOW_TASK_RENAMES = {
    "Quicklook Logs": "Quicklook Logs Interpretation",
    "Aramco Approved Picks": "Aramco Picks",
    "Flowback": "Flowback Results",
    "Flow Back": "Flowback Results",
    "Post Test": "Flowback Results",
}

DYNAMIC_FIELD_OVERVIEW_MAP = {
    "lead_piip_gas_mean": "lead_ogip",
    "pre_drill_piip_gas_mean": "pre_drill_estimation",
    "post_drill_piip_gas_mean": "post_drill_estimation",
    "resource_update_gas_mean": "post_drill_estimation",
    "presence_cos": "derisking",
    "quicklook_pay_thickness_ft": "quick_look_pay",
    "quicklook_average_porosity_pct": "quick_look_porosity",
    "quicklook_average_swt_pct": "quick_look_swt",
    "flowback_gas_rate_mmscfd": "flowback_results",
}

_OVERVIEW_ALLOWED_FIELDS = {
    "derisking", "ogip", "lead_ogip", "preliminary_resource_estimation", "pre_drill_estimation",
    "post_drill_estimation", "reservoir_pressure", "reservoir_gradient",
    "flowback_results", "pay", "porosity", "swt",
    "quick_look_pay", "quick_look_porosity", "quick_look_swt",
}


# ---------------------------------------------------------------------------
# Templates + seeding
# ---------------------------------------------------------------------------

def get_templates(session) -> List[Dict[str, Any]]:
    """Return all task templates ordered by sequence."""
    return db.fetch_all(session, "SELECT * FROM task_templates ORDER BY sequence_no")


def seed_templates(session) -> None:
    """Insert the canonical PIPELINE_TEMPLATES if the templates table is empty."""
    count = db.fetch_one(session, "SELECT COUNT(*) AS c FROM task_templates")["c"]
    if count:
        return
    db.execute_many(session, """
        INSERT INTO task_templates (
            template_id, sequence_no, task_name, stage_group, default_role,
            default_duration_days, depends_on_template_id, branch_type, mandatory_output
        ) VALUES (:template_id, :sequence_no, :task_name, :stage_group, :default_role,
                  :default_duration_days, :depends_on_template_id, :branch_type, :mandatory_output)
    """, [
        {
            "template_id": t[0], "sequence_no": idx + 1, "task_name": t[1],
            "stage_group": t[2], "default_role": t[3], "default_duration_days": t[4],
            "depends_on_template_id": t[5], "branch_type": t[6], "mandatory_output": t[7],
        }
        for idx, t in enumerate(PIPELINE_TEMPLATES)
    ])


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
    """Create a project and materialize its 32 workflow tasks; return project_id."""
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

    templates = list(get_templates(session))
    if not templates:
        raise RuntimeError("Workflow templates are not available.")
    first_template = next((t for t in templates if t["stage_group"] in BP_EXECUTION_STAGES), templates[0]) if pipeline_type == "bp" else templates[0]

    try:
        return _insert_project_with_tasks(session, project_name, start_date, target_date, changed_by,
                                          lead_x, lead_y, year_val, bp_enabled, active_well_enabled,
                                          pipeline_type, templates, first_template, now)
    except IntegrityError as exc:
        # UNIQUE(project_name) race lost to a concurrent insert.
        if "unique" in str(getattr(exc, "orig", None) or exc).lower():
            raise ValueError("A lead / well with this name already exists.") from exc
        raise


def _insert_project_with_tasks(session, project_name, start_date, target_date, changed_by, lead_x, lead_y,
                               year_val, bp_enabled, active_well_enabled, pipeline_type, templates,
                               first_template, now):
    """Insert the project row plus its per-template tasks in one locked transaction."""
    with db.write_transaction(session):
        result = db.execute(session, """
            INSERT INTO projects (
                project_name, overall_status, current_stage, current_task, current_owner,
                drill_result, start_date, target_date, current_stage_started_at, last_updated,
                lead_folder_path, lead_x, lead_y, business_plan_enabled, business_plan_year,
                active_well_enabled, pipeline_type
            ) VALUES (:project_name, :overall_status, :current_stage, :current_task, :current_owner,
                      :drill_result, :start_date, :target_date, :stage_started_at, :last_updated,
                      :lead_folder_path, :lead_x, :lead_y, :business_plan_enabled, :business_plan_year,
                      :active_well_enabled, :pipeline_type)
        """, {
            "project_name": project_name, "overall_status": "In Progress",
            "current_stage": first_template["stage_group"], "current_task": first_template["task_name"],
            "current_owner": None, "drill_result": None, "start_date": start_date,
            "target_date": target_date, "stage_started_at": start_date, "last_updated": now,
            "lead_folder_path": folders.default_lead_folder_path(project_name),
            "lead_x": lead_x or None, "lead_y": lead_y or None,
            "business_plan_enabled": bp_enabled, "business_plan_year": year_val,
            "active_well_enabled": 1 if active_well_enabled else 0, "pipeline_type": pipeline_type,
        })
        project_id = result.lastrowid  # PG: use RETURNING when on Postgres
        try:
            start_dt = date.fromisoformat(start_date)
        except Exception:
            start_dt = date.today()
        first_task_id = None
        first_sequence = first_template["sequence_no"]
        for row in templates:
            is_bp_stage = row["stage_group"] in BP_EXECUTION_STAGES
            if pipeline_type == "bp" and not is_bp_stage:
                initial_status = "Not Applicable"
            elif pipeline_type == "prospect" and is_bp_stage:
                initial_status = "Not Assigned"
            else:
                initial_status = "Assigned" if row["sequence_no"] == first_sequence else "Not Assigned"
            planned_start = start_dt.isoformat() if row["sequence_no"] == first_sequence else None
            planned_finish = ((start_dt + timedelta(days=row["default_duration_days"])).isoformat()
                              if row["sequence_no"] == first_sequence else None)
            task_result = db.execute(session, """
                INSERT INTO project_tasks (
                    project_id, template_id, sequence_no, task_name, stage_group, assigned_to,
                    backup_owner, approver, status, planned_start, actual_start, planned_finish,
                    actual_finish, output_notes, comments, priority, business_plan_enabled,
                    business_plan_year, is_active, last_updated
                ) VALUES (:project_id, :template_id, :sequence_no, :task_name, :stage_group, :assigned_to,
                          :backup_owner, :approver, :status, :planned_start, :actual_start, :planned_finish,
                          :actual_finish, :output_notes, :comments, :priority, :business_plan_enabled,
                          :business_plan_year, 1, :last_updated)
            """, {
                "project_id": project_id, "template_id": row["template_id"],
                "sequence_no": row["sequence_no"], "task_name": row["task_name"],
                "stage_group": row["stage_group"], "assigned_to": None, "backup_owner": None,
                "approver": None, "status": initial_status, "planned_start": planned_start,
                "actual_start": None, "planned_finish": planned_finish, "actual_finish": None,
                "output_notes": row["mandatory_output"], "comments": None, "priority": "Medium",
                "business_plan_enabled": bp_enabled, "business_plan_year": year_val,
                "last_updated": now,
            })
            if row["sequence_no"] == first_sequence:
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
                task_name=first_template["task_name"],
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
        WHERE project_id = :project_id AND is_active = 1 AND status IN ('Assigned','In Progress','Ready for Review','Under Review','Ready for Approval','Returned for Update')
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

    row = db.fetch_one(session, """
        SELECT * FROM project_tasks
        WHERE project_id = :project_id AND is_active = 1 AND status NOT IN ('Approved','Not Applicable','Complete') AND sequence_no > :current_seq
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
    has_existing_bp_work = any((task["status"] or "") not in {"Not Applicable", "Not Assigned"} for task in bp_tasks)
    for index, task in enumerate(bp_tasks):
        old_status = task["status"] or "Not Assigned"
        # A promoted prospect normally has BP tasks in Not Assigned state. Activate
        # the first BP task exactly once; leave any existing BP progress untouched.
        should_activate_first = index == 0 and not has_existing_bp_work and old_status in {"Not Applicable", "Not Assigned"}
        if old_status == "Not Applicable" or should_activate_first:
            next_status = "Assigned" if should_activate_first else "Not Assigned"
            db.execute(session, """
                UPDATE project_tasks
                SET status = :status, business_plan_enabled = 1, business_plan_year = :year, last_updated = :now, revision = COALESCE(revision, 0) + 1
                WHERE task_id = :task_id
            """, {"status": next_status, "year": year_val, "now": now, "task_id": task["task_id"]})
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
    status = str(payload.get("status") or "Not Assigned")
    assigned_to = str(payload.get("assigned_to") or "").strip()
    comments = str(payload.get("comments") or "").strip()
    priority = str(payload.get("priority") or "Medium").strip().title()
    if priority not in {"Low", "Medium", "High"}:
        priority = "Medium"
    if status not in STATUSES:
        raise ValueError("Invalid component status.")

    result: Dict[str, Any] = {}
    with db.write_transaction(session):
        task = get_task(session, task_id)
        if not task:
            raise ValueError("Component not found.")
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


def _final_reservoir_cos_value(session, project_id):
    """Return the last completed Reservoir CoS row value (the final Reservoir CoS)."""
    raw = _task_field_value(session, project_id, "Reservoir CoS", "reservoir_cos_rows")
    if not raw:
        return ""
    try:
        rows = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(rows, list):
        return ""
    for row in reversed(rows):
        value = (row or {}).get("reservoir_cos_pct")
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def recalculate_presence_cos(session, project_id, changed_by="System"):
    """Persist the automatic Presence CoS reading for one project.

    The final Reservoir CoS is the last completed row in Reservoir CoS. Presence
    CoS is read-only in the UI and always equals Reservoir x Trap x Seal. No
    commit here -- the caller's transaction owns the commit.
    """
    target = db.fetch_one(session, """
        SELECT task_id FROM project_tasks
        WHERE project_id = :project_id AND task_name = 'Presence CoS Evaluation'
        ORDER BY task_id DESC LIMIT 1
    """, {"project_id": project_id})
    if not target:
        return {}
    reservoir = _final_reservoir_cos_value(session, project_id)
    trap = _task_field_value(session, project_id, "Trap CoS", "trap_cos_pct")
    seal = _task_field_value(session, project_id, "Seal CoS", "seal_cos_pct")
    values = cos.calculate_presence_cos(reservoir, trap, seal)
    now = utc_now_str()
    existing = get_task_dynamic_fields(session, target["task_id"])
    changed = any(str(existing.get(k, "")) != str(v) for k, v in values.items())
    for key, value in values.items():
        db.execute(session, """
            INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
            VALUES (:task_id, :field_key, :field_value, :now)
            ON CONFLICT(task_id, field_key) DO UPDATE SET
                field_value = excluded.field_value, updated_at = excluded.updated_at
        """, {"task_id": target["task_id"], "field_key": key, "field_value": str(value), "now": now})
    update_project_overview_fields(session, project_id, {"derisking": values.get("presence_cos", "")})
    if changed:
        note = "Automatically recalculated from final Reservoir CoS x Trap CoS x Seal CoS."
        log_task_event(session, target["task_id"], project_id, "Presence CoS Evaluation",
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
