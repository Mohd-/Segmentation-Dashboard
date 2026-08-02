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


def _first_filled(*values) -> str:
    """Return the first non-blank value as a stripped string, else ''."""
    for value in values:
        text_value = "" if value is None else str(value).strip()
        if text_value:
            return text_value
    return ""


def _sarh_phase_fluid(sarh_by_phase, phase) -> str:
    """Fluid of the SARH formation row at ``phase`` in a {phase: row} map.

    Tolerant of a missing/None map or a phase whose row is absent or lacks a
    ``fluid`` key (returns '' in every such case)."""
    row = (sarh_by_phase or {}).get(phase)
    if not row:
        return ""
    getter = getattr(row, "get", None)
    return _first_filled(getter("fluid")) if callable(getter) else ""


def resolve_well_fluid(fields, sarh_by_phase) -> str:
    """Resolve the WELL's fluid down the canonical ladder, first non-blank wins.

    The well inherits SARH's per-formation fluid; the step-level Quicklook /
    Final Log Analysis fluid selects are gone, so their EAV keys survive only as
    read-fallbacks for wells written before the multi-formation editor existed.
    Precedence:

    1. SARH formation row's fluid at phase 'final' (the petrophysical authority)
    2. legacy EAV ``final_fluid_type`` (old-well fallback; nothing writes it now)
    3. EAV ``resource_update_fluid_type`` (the SAD Update step -- or, on a well
       written before the v4 merge, the retired Resource Assessment Update)
    4. EAV ``post_drill_fluid_type`` (the SAD Model step -- or, pre-v4, the
       retired Post-Drilling Resource Assessment)
    5. SARH formation row's fluid at phase 'quicklook'
    6. legacy EAV ``quicklook_fluid_type`` (old-well fallback)

    ``fields`` is the task-EAV dict; ``sarh_by_phase`` is a {phase: formation
    row (dict-like)} map for the well's SARH rows -- both tolerated as
    missing/None. Returns '' when no source is filled."""
    fields = fields or {}
    return _first_filled(
        _sarh_phase_fluid(sarh_by_phase, "final"),
        fields.get("final_fluid_type"),
        fields.get("resource_update_fluid_type"),
        fields.get("post_drill_fluid_type"),
        _sarh_phase_fluid(sarh_by_phase, "quicklook"),
        fields.get("quicklook_fluid_type"),
    )


# The task-level inputs the BP-well readers compose from, at read time (there
# is no stored overview mirror): fluid precedence (final -> resource_update ->
# post_drill -> quicklook, mirroring the formations "actual" phase precedence
# with final as the petrophysical authority), Reservoir CoS rows (AR number +
# final reservoir pct), the Trap/Seal CoS inputs of Total CoS, the mean-PIIP
# assessments, and the classification. Classification now reads from the BP
# Execution Gate step (bp_gate_classification), falling back to the legacy
# GHEER key (gheer_classification) so older wells still resolve.
_BP_TASK_FIELD_KEYS = [
    "final_fluid_type", "resource_update_fluid_type",
    "post_drill_fluid_type", "quicklook_fluid_type", "reservoir_cos_rows",
    "trap_cos_pct", "seal_cos_pct",
    "lead_piip_gas_mean", "pre_drill_piip_gas_mean",
    "post_drill_piip_gas_mean", "resource_update_gas_mean",
    "bp_gate_classification", "gheer_classification",
]


def _bp_task_fields(session, project_ids):
    """Batched {project_id: {field_key: value}} of _BP_TASK_FIELD_KEYS.

    ONE query for the whole id list; shared by the business-plan scorecard and
    the Portfolio. The map is keyed by FIELD key, not by task name, so it is
    naturally indifferent to which step a value was entered on -- which is what
    makes the v4 step merges transparent here: a legacy well's
    ``post_drill_piip_gas_mean`` sits on the retired "Post-Drilling Resource
    Assessment" row, a new one's on "SAD Model", and both answer to the same
    key.

    RETIRED-INCLUSIVE for exactly that reason: inactive rows are read too (a
    retired step's inputs must not vanish from the Portfolio), with
    ``ORDER BY pt.is_active`` putting them FIRST so an ACTIVE row is folded in
    last -- surviving step wins, retired step is the fallback. Folding is
    first-NON-BLANK-wins (_fold_task_field_rows), the same rule every other
    read ladder here uses, so a blank on the surviving step cannot erase a
    legacy well's stored number.
    Deterministic on legacy duplicate rows within a group: higher task_id wins.
    """
    if not project_ids:
        return {}
    rows = db.fetch_all(session, """
        SELECT pt.project_id, tdf.field_key, tdf.field_value
        FROM project_tasks pt
        JOIN task_dynamic_fields tdf ON tdf.task_id = pt.task_id
        WHERE pt.project_id IN :project_ids
          AND tdf.field_key IN :field_keys
        ORDER BY pt.is_active, pt.task_id
    """, {"project_ids": list(project_ids), "field_keys": _BP_TASK_FIELD_KEYS})
    return fold_task_field_rows(rows)


def fold_task_field_rows(rows):
    """Fold (project_id, field_key, field_value) rows into {pid: {key: value}}.

    Later rows win, EXCEPT that a blank never displaces an already-recorded
    non-blank -- the same first-non-blank-wins rule as _first_filled and
    _OVERVIEW_READ_SOURCES. Callers order their query so the
    lowest-precedence rows come first (retired/inactive task rows before
    active ones). Shared with portfolio_export._task_fields so the Portfolio
    UI and the Excel export fold identically.
    """
    fields: Dict[int, Dict[str, str]] = {}
    for row in rows:
        bucket = fields.setdefault(row["project_id"], {})
        value = row["field_value"] or ""
        if value or row["field_key"] not in bucket:
            bucket[row["field_key"]] = value
    return fields


def sarh_formations_by_phase(session, project_ids) -> Dict[int, Dict[str, dict]]:
    """Batched {project_id: {phase: project_formations row}} for formation SARH.

    ONE query over the whole id list, restricted to the canonical SARH row --
    the only formation the well-fluid ladder (resolve_well_fluid) reads. Shared
    with portfolio_export (whose _sarh_formations now delegates here) so the two
    surfaces resolve fluid from the exact same rows.
    """
    if not project_ids:
        return {}
    rows = db.fetch_all(session, """
        SELECT * FROM project_formations
        WHERE project_id IN :project_ids AND formation = 'SARH'
    """, {"project_ids": list(project_ids)})
    result: Dict[int, Dict[str, dict]] = {}
    for row in rows:
        result.setdefault(row["project_id"], {})[row["phase"]] = row
    return result


def get_business_plan_rows(session):
    """Return BP-enabled well rows for the business-plan scorecard.

    OGIP and chance-of-success columns are composed from the task inputs at
    read time (one batched _bp_task_fields query). Post-drill OGIP follows the
    latest-assessment-first precedence: the SAD Update's resource_update trio
    beats the SAD Model's post_drill trio (pre-v4 wells carry the same two key
    families on the retired Resource Assessment Update / Post-Drilling Resource
    Assessment rows, which _bp_task_fields folds in under the same keys).
    """
    rows = db.fetch_all(session, """
        SELECT p.project_id,
               p.business_plan_year AS year,
               p.project_name AS well_name,
               COALESCE(p.active_well_enabled, 0) AS active_well_enabled
        FROM projects p
        WHERE COALESCE(p.archived, 0) = 0 AND COALESCE(p.business_plan_enabled, 0) = 1
        ORDER BY p.business_plan_year, p.project_name COLLATE NOCASE
    """)  # PG: COLLATE NOCASE
    task_fields = _bp_task_fields(session, [r["project_id"] for r in rows])
    result = []
    for item in rows:
        fields = task_fields.get(item["project_id"], {})
        item["pre_drill_ogip"] = _first_filled(fields.get("pre_drill_piip_gas_mean"))
        item["post_drill_ogip"] = _first_filled(fields.get("resource_update_gas_mean"),
                                                fields.get("post_drill_piip_gas_mean"))
        item["chance_of_success"] = workflow.total_cos_from_fields(
            fields.get("reservoir_cos_rows"), fields.get("trap_cos_pct"), fields.get("seal_cos_pct"))
        # Classification should be blank if required inputs are incomplete.
        class_ogip = item["post_drill_ogip"] or item["pre_drill_ogip"]
        item["segment_class"] = cos.segment_class(class_ogip, item["chance_of_success"])
        result.append(item)
    return result


def _portfolio_projects(session):
    """Return the Portfolio membership rows (shared reader, imported by export).

    A record belongs in the Portfolio when it is either a BP-enabled well OR a
    fully-matured lead: a prospect whose every active prospect-stage task is
    Approved (the same completion notion as the derived overall_status). The
    mature-lead arm is expressed as a NOT EXISTS over the still-open prospect
    tasks so a brand-new prospect (12 un-approved steps) stays out until it
    completes. Ordered by BP year then name; mature leads carry a NULL year and
    sort together at the front.
    """
    return db.fetch_all(session, """
        SELECT p.project_id,
               p.project_name,
               p.business_plan_year AS year,
               COALESCE(p.pipeline_type, 'prospect') AS pipeline_type,
               COALESCE(p.business_plan_enabled, 0) AS business_plan_enabled,
               COALESCE(p.active_well_enabled, 0) AS active_well_enabled
        FROM projects p
        WHERE COALESCE(p.archived, 0) = 0
          AND (
              COALESCE(p.business_plan_enabled, 0) = 1
              OR (
                  LOWER(COALESCE(p.pipeline_type, 'prospect')) = 'prospect'
                  AND NOT EXISTS (
                      SELECT 1 FROM project_tasks pt
                      WHERE pt.project_id = p.project_id
                        AND pt.is_active = 1
                        AND pt.stage_group IN :prospect_stages
                        AND pt.status != 'Approved'
                  )
              )
          )
        ORDER BY p.business_plan_year, p.project_name COLLATE NOCASE
    """, {"prospect_stages": list(workflow.PROSPECT_STAGES)})  # PG: COLLATE NOCASE


def _approval_to_stake_map(session, project_ids):
    """Batched {project_id: bool} -- whether 'Approval to Stake' is Approved.

    One GROUP BY query over the whole id list; a project is Staked once any of
    its active 'Approval to Stake' task rows reaches Approved (MAX collapses
    legacy duplicate rows). SQLite has no native boolean, so the truthiness is
    computed with a CASE and read back as an int.
    """
    if not project_ids:
        return {}
    rows = db.fetch_all(session, """
        SELECT pt.project_id,
               MAX(CASE WHEN pt.status = 'Approved' THEN 1 ELSE 0 END) AS staked
        FROM project_tasks pt
        WHERE pt.project_id IN :project_ids AND pt.is_active = 1
          AND pt.task_name = 'Approval to Stake'
        GROUP BY pt.project_id
    """, {"project_ids": list(project_ids)})
    return {row["project_id"]: bool(row["staked"]) for row in rows}


def record_status(fields, staked, fluid=None):
    """Portfolio status of a record from its task inputs (Proposed/Staked/fluid).

    Once a fluid is recorded that value shows; otherwise the record is 'Staked'
    when its 'Approval to Stake' step is Approved, else 'Proposed' (the default
    for every portfolio record). Replaces the old 'Not Drilled Yet' fallback.

    ``fluid`` may be pre-resolved by the caller via resolve_well_fluid (the full
    SARH-aware ladder). When left None, this degrades to the legacy EAV-only
    lookup (final -> resource_update -> post_drill -> quicklook) so a caller that
    has not fetched the SARH formation rows still gets a workable status.
    """
    if fluid is None:
        fluid = _first_filled(fields.get("final_fluid_type"),
                              fields.get("resource_update_fluid_type"),
                              fields.get("post_drill_fluid_type"),
                              fields.get("quicklook_fluid_type"))
    if fluid:
        return fluid
    return "Staked" if staked else "Proposed"


def get_portfolio_rows(session, year="All", activity="All"):
    """Return the filtered Portfolio analysis rows + summary.

    Membership is BP-enabled wells PLUS fully-matured leads (see
    _portfolio_projects); each row carries its pipeline_type and an
    is_mature_lead flag (1 for the prospect rows). Every column is composed
    from the task inputs at read time (one batched _bp_task_fields query, one
    _approval_to_stake_map query): well name, gas field (project-name prefix
    before the first hyphen), seismic block (FIRST non-empty Reservoir CoS AR
    number mapped through config.AR_TO_SEISMIC_BLOCK, raw AR fallback),
    classification (BP Execution Gate input -> legacy GHEER fallback), BP year,
    status (fluid -> 'Staked' when Approval to Stake is approved -> 'Proposed'),
    raw fluid (resolve_well_fluid: SARH 'final'-phase formation fluid -> legacy
    final_fluid_type -> resource_update -> post_drill -> SARH 'quicklook'-phase
    formation fluid -> legacy quicklook_fluid_type, '' when unset), mean OGIP
    (latest assessment first: resource update -> post-drill -> pre-drill ->
    lead) and total chance of success (workflow.total_cos_from_fields). One
    batched SARH-formation fetch (sarh_formations_by_phase) feeds the fluid
    ladder. Filters apply here so the summary reflects the displayed rows.
    """
    selected_year = str(year or "All").strip()
    selected_activity = str(activity or "All").strip()
    if selected_year != "All":
        try:
            selected_year_int = int(selected_year)
        except (TypeError, ValueError):
            raise ValueError("Select a business plan year from 1990 to 2040.")
        # Floor is 1990, not 2026: imported historical wells carry a
        # pre-2026 business_plan_year and must be filterable in the Portfolio
        # (the UI year select offers the distinct years present in the data --
        # static/js/views/portfolio.js).
        if selected_year_int < 1990 or selected_year_int > 2040:
            raise ValueError("Select a business plan year from 1990 to 2040.")
    else:
        selected_year_int = None

    if selected_activity not in {"All", "Active", "Non-Active"}:
        selected_activity = "All"

    projects = _portfolio_projects(session)
    project_ids = [p["project_id"] for p in projects]
    task_fields = _bp_task_fields(session, project_ids)
    stake_map = _approval_to_stake_map(session, project_ids)
    sarh_formations = sarh_formations_by_phase(session, project_ids)

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
        well_fluid = resolve_well_fluid(fields, sarh_formations.get(item["project_id"], {}))
        # Mature leads are the non-BP-enabled members of the portfolio: they got
        # in via the fully-approved-prospect arm of _portfolio_projects. BP-enabled
        # records (whether or not their pipeline_type has flipped to 'bp') are wells.
        is_mature_lead = 0 if int(item.get("business_plan_enabled") or 0) == 1 else 1
        ar_number = workflow.first_reservoir_cos_row_value(
            fields.get("reservoir_cos_rows"), "seismic_volume_ar_number")
        mean_ogip = _first_filled(fields.get("resource_update_gas_mean"),
                                  fields.get("post_drill_piip_gas_mean"),
                                  fields.get("pre_drill_piip_gas_mean"),
                                  fields.get("lead_piip_gas_mean"))
        row = {
            "project_id": item["project_id"],
            "well_name": item["project_name"],
            "gas_field": folders.parse_field_and_well(item["project_name"])[0],
            "seismic_block": config.AR_TO_SEISMIC_BLOCK.get(ar_number, ar_number) if ar_number else "",
            "classification": _first_filled(fields.get("bp_gate_classification"),
                                            fields.get("gheer_classification")),
            "year": item["year"],
            "status": record_status(fields, stake_map.get(item["project_id"], False),
                                    fluid=well_fluid),
            "fluid": well_fluid,
            "mean_ogip": mean_ogip,
            "total_cos": workflow.total_cos_from_fields(
                fields.get("reservoir_cos_rows"), fields.get("trap_cos_pct"), fields.get("seal_cos_pct")),
            "active_well_enabled": int(item["active_well_enabled"] or 0),
            "pipeline_type": str(item.get("pipeline_type") or "prospect").lower(),
            "is_mature_lead": is_mature_lead,
        }
        ogip_value = to_float_or_none(row["mean_ogip"])
        if ogip_value is not None:
            cumulative_ogip += ogip_value
        filtered.append(row)

    return {
        "rows": filtered,
        "summary": {
            "business_plan_wells": sum(1 for r in filtered if r["is_mature_lead"] == 0),
            "mature_leads": sum(1 for r in filtered if r["is_mature_lead"] == 1),
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
