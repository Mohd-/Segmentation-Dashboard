"""Read-only dashboards and aggregate reporting queries.

What belongs here:
- The dashboard metrics, monthly progress trend, bottleneck/attention lists,
  well-overview / business-plan / portfolio rows and the activity log.

What does NOT belong here:
- Any writes or lifecycle logic (workflow.py).

Style: the aggregate queries are kept as readable textual SQL with named binds,
executed through the shared ``db.fetch_one``/``db.fetch_all`` helpers (the one
query idiom used across the codebase) rather than ORM expressions -- they are
easier to review and tune in this form.

SQLite-only constructs to revisit for a future Postgres pass are flagged with
``# PG:`` comments (e.g. ``substr(...)``, ``COLLATE NOCASE``, boolean-ish text
comparisons).
"""
from __future__ import annotations

from typing import Dict

import config
import cos
import db
import folders
import workflow
from helpers import to_float_or_none


def dashboard_metrics(session):
    """Return (metrics, stage_counts, owner_workload) for the dashboard header.

    Stage/owner/status come from workflow.get_projects: the board pointers are
    derived from project_tasks at read time (never stored), so the dashboard
    always agrees with the board.
    """
    rows = workflow.get_projects(session)
    metrics = {
        "Completed Wells": sum(1 for row in rows if row["overall_status"] == "Completed"),
        # v17 lifecycle: 'Ready' is the awaiting-approval state (formerly the
        # Ready for Review / Under Review / Ready for Approval trio).
        "Components Ready": db.fetch_one(session,
            """
            SELECT COUNT(*) AS c
            FROM project_tasks pt
            JOIN projects p ON p.project_id = pt.project_id
            WHERE pt.is_active = 1 AND pt.status = 'Ready'
              AND COALESCE(p.archived, 0) = 0
            """)["c"],
    }
    stage_counts = {stage: 0 for stage in workflow.STAGE_ORDER}
    owner_workload: Dict[str, int] = {}
    for row in rows:
        stage = row["current_stage"]
        owner = row["current_owner"]
        if stage in stage_counts:
            stage_counts[stage] += 1
        if owner:
            owner_workload[owner] = owner_workload.get(owner, 0) + 1
    return metrics, stage_counts, owner_workload


def monthly_progress_metrics(session, limit=12):
    """Return per-month leads/components/BP-additions/completions, oldest first."""
    rows = db.fetch_all(session, """
        WITH activity AS (
            SELECT
                substr(changed_at, 1, 7) AS month,  -- PG: use to_char(changed_at::date, 'YYYY-MM')
                SUM(CASE WHEN action_type = 'Lead Created' THEN 1 ELSE 0 END) AS leads_created,
                SUM(CASE WHEN action_type = 'Well Added to BP' THEN 1 ELSE 0 END) AS wells_added_to_bp,
                SUM(CASE WHEN action_type IN ('Task Update', 'Component Update')
                              AND new_status = 'Approved' THEN 1 ELSE 0 END) AS components_completed
            FROM task_history
            WHERE changed_at IS NOT NULL
            GROUP BY substr(changed_at, 1, 7)
        ), completed AS (
            -- Bucketed by completed_at (stamped once at the completion
            -- transition; _sync_completed_at keeps it in lockstep with the
            -- derived completion state), so later edits to a completed well
            -- cannot move it between months.
            SELECT substr(completed_at, 1, 7) AS month, COUNT(*) AS completed_wells
            FROM projects
            WHERE completed_at IS NOT NULL AND completed_at != ''
            GROUP BY substr(completed_at, 1, 7)
        )
        SELECT activity.month, activity.leads_created, activity.wells_added_to_bp,
               activity.components_completed, COALESCE(completed.completed_wells, 0) AS completed_wells
        FROM activity
        LEFT JOIN completed ON completed.month = activity.month
        ORDER BY activity.month DESC
        LIMIT :limit
    """, {"limit": limit})
    monthly = []
    for row in rows:
        leads = int(row["leads_created"] or 0)
        completed_components = int(row["components_completed"] or 0)
        added_to_bp = int(row["wells_added_to_bp"] or 0)
        monthly.append({
            "month": row["month"] or "Unknown",
            "leads_created": leads,
            "wells_created": leads,
            "wells_completed": int(row["completed_wells"] or 0),
            "components_completed": completed_components,
            "wells_added_to_bp": added_to_bp,
            "progress_index": leads + completed_components + added_to_bp,
        })
    return list(reversed(monthly))


def get_business_plan_rows(session):
    """Return BP-enabled well rows for the business-plan scorecard."""
    rows = db.fetch_all(session, """
        SELECT p.project_id,
               p.business_plan_year AS year,
               p.project_name AS well_name,
               COALESCE(o.pre_drill_estimation, '') AS pre_drill_ogip,
               COALESCE(o.post_drill_estimation, '') AS post_drill_ogip,
               COALESCE(o.derisking, '') AS chance_of_success,
               COALESCE(p.active_well_enabled, 0) AS active_well_enabled
        FROM projects p
        LEFT JOIN project_overview o ON o.project_id = p.project_id
        WHERE COALESCE(p.archived, 0) = 0 AND COALESCE(p.business_plan_enabled, 0) = 1
        ORDER BY p.business_plan_year, p.project_name COLLATE NOCASE
    """)  # PG: COLLATE NOCASE
    result = []
    for item in rows:
        # Classification should be blank if required inputs are incomplete.
        class_ogip = item.get("post_drill_ogip") or item.get("pre_drill_ogip")
        item["segment_class"] = cos.segment_class(class_ogip, item.get("chance_of_success"))
        result.append(item)
    return result


def _first_filled(*values) -> str:
    """Return the first non-blank value as a stripped string, else ''."""
    for value in values:
        text_value = "" if value is None else str(value).strip()
        if text_value:
            return text_value
    return ""


def _portfolio_task_fields(session, project_ids):
    """Batched {project_id: {field_key: value}} for the portfolio's task-level
    inputs (fluid precedence + Reservoir CoS rows).

    Queried directly from active task rows -- NOT the overview mirror, whose
    last-write-wins semantics cannot express the final-beats-quicklook
    precedence. Deterministic on legacy duplicate rows: higher task_id wins.
    """
    if not project_ids:
        return {}
    rows = db.fetch_all(session, """
        SELECT pt.project_id, tdf.field_key, tdf.field_value
        FROM project_tasks pt
        JOIN task_dynamic_fields tdf ON tdf.task_id = pt.task_id
        WHERE pt.project_id IN :project_ids AND pt.is_active = 1
          AND tdf.field_key IN ('final_fluid_type', 'quicklook_fluid_type', 'reservoir_cos_rows')
        ORDER BY pt.task_id
    """, {"project_ids": list(project_ids)})
    fields: Dict[int, Dict[str, str]] = {}
    for row in rows:
        fields.setdefault(row["project_id"], {})[row["field_key"]] = row["field_value"] or ""
    return fields


def get_portfolio_rows(session, year="All", activity="All"):
    """Return the filtered Portfolio analysis rows (BP-enabled wells) + summary.

    Each row carries exactly the 8 user-facing columns (WS7): well name,
    gas field (project-name prefix before the first hyphen), seismic block
    (last non-empty Reservoir CoS AR number mapped through
    config.SEISMIC_BLOCK_NAMES, raw AR fallback), classification (GHEER,
    mirrored to overview), BP year, fluid (final -> quicklook ->
    'Not Drilled Yet'), mean OGIP (post-drill -> pre-drill -> lead) and total
    chance of success (overview.derisking). Filters apply here so the summary
    reflects the displayed rows.
    """
    selected_year = str(year or "All").strip()
    selected_activity = str(activity or "All").strip()
    if selected_year != "All":
        try:
            selected_year_int = int(selected_year)
        except (TypeError, ValueError):
            raise ValueError("Select a business plan year from 2026 to 2040.")
        if selected_year_int < 2026 or selected_year_int > 2040:
            raise ValueError("Select a business plan year from 2026 to 2040.")
    else:
        selected_year_int = None

    if selected_activity not in {"All", "Active", "Non-Active"}:
        selected_activity = "All"

    projects = db.fetch_all(session, """
        SELECT p.project_id,
               p.project_name,
               p.business_plan_year AS year,
               COALESCE(p.active_well_enabled, 0) AS active_well_enabled,
               COALESCE(o.classification, '') AS classification,
               COALESCE(o.derisking, '') AS total_cos,
               COALESCE(o.post_drill_estimation, '') AS post_drill_estimation,
               COALESCE(o.pre_drill_estimation, '') AS pre_drill_estimation,
               COALESCE(o.lead_ogip, '') AS lead_ogip
        FROM projects p
        LEFT JOIN project_overview o ON o.project_id = p.project_id
        WHERE COALESCE(p.archived, 0) = 0 AND COALESCE(p.business_plan_enabled, 0) = 1
        ORDER BY p.business_plan_year, p.project_name COLLATE NOCASE
    """)  # PG: COLLATE NOCASE
    task_fields = _portfolio_task_fields(session, [p["project_id"] for p in projects])

    filtered = []
    cumulative_ogip = 0.0
    for item in projects:
        if selected_year_int is not None and int(item.get("year") or 0) != selected_year_int:
            continue
        is_active = int(item.get("active_well_enabled") or 0) == 1
        if selected_activity == "Active" and not is_active:
            continue
        if selected_activity == "Non-Active" and is_active:
            continue

        fields = task_fields.get(item["project_id"], {})
        ar_number = workflow.last_reservoir_cos_row_value(
            fields.get("reservoir_cos_rows"), "seismic_volume_ar_number")
        mean_ogip = _first_filled(item["post_drill_estimation"],
                                  item["pre_drill_estimation"], item["lead_ogip"])
        row = {
            "project_id": item["project_id"],
            "well_name": item["project_name"],
            "gas_field": folders.parse_field_and_well(item["project_name"])[0],
            "seismic_block": config.SEISMIC_BLOCK_NAMES.get(ar_number, ar_number) if ar_number else "",
            "classification": item["classification"],
            "year": item["year"],
            "fluid": _first_filled(fields.get("final_fluid_type"),
                                   fields.get("quicklook_fluid_type")) or "Not Drilled Yet",
            "mean_ogip": mean_ogip,
            "total_cos": item["total_cos"],
            "active_well_enabled": int(item["active_well_enabled"] or 0),
        }
        ogip_value = to_float_or_none(row["mean_ogip"])
        if ogip_value is not None:
            cumulative_ogip += ogip_value
        filtered.append(row)

    return {
        "rows": filtered,
        "summary": {
            "business_plan_wells": len(filtered),
            "cumulative_ogip": round(cumulative_ogip, 1),
        },
    }


def get_activity_log(session, project_id=None, limit=500):
    """Return recent task_history rows (optionally filtered to one project).

    history_id breaks ties for events sharing the same changed_at second, so
    the log order is deterministic (newest insert first) instead of depending
    on the database's whim.
    """
    if project_id:
        return db.fetch_all(session, """
            SELECT th.*, p.project_name
            FROM task_history th
            LEFT JOIN projects p ON p.project_id = th.project_id
            WHERE th.project_id = :project_id
            ORDER BY th.changed_at DESC, th.history_id DESC LIMIT :limit
        """, {"project_id": project_id, "limit": limit})
    return db.fetch_all(session, """
        SELECT th.*, p.project_name
        FROM task_history th
        LEFT JOIN projects p ON p.project_id = th.project_id
        ORDER BY th.changed_at DESC, th.history_id DESC LIMIT :limit
    """, {"limit": limit})
