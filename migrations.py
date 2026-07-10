"""Numbered schema/data migration framework.

What belongs here:
- Creating a fresh schema (via ``models.Base.metadata.create_all``), the legacy
  column-ensuring ALTERs for pre-existing databases, and the numbered
  ``MIGRATIONS`` steps that upgrade an older database to the current version.

What does NOT belong here:
- Runtime domain logic (workflow.py) -- although a migration may call domain
  helpers (e.g. presence-CoS recalculation) to reproduce historical behavior.

Adoption rules (behavior preserved from the old bootstrap):
- A brand-new (empty) database: ``create_all`` + seed templates + set the schema
  version straight to the latest, WITHOUT replaying historical steps (they would
  be no-ops on empty data).
- An existing database already at the latest version: adopt as-is.
- An existing database below the latest version: run the ported "consolidate to
  v15" step, which reproduces the old ``apply_workflow_updates`` exactly.

Current latest version is 19 (v19 backfills well-level SARH formation rows from
the legacy quicklook/final task fields; see ``_upgrade_to_v19``.
``_upgrade_to_v16`` remains the template to copy when adding future steps).

Concurrency guard: ``run`` acquires the database write lock UPFRONT via
``db.begin_write`` (SQLite ``BEGIN IMMEDIATE``) before the ensure/seed writes
and again before each numbered migration step, so concurrent processes cannot
interleave migration writes. On Postgres ``begin_write`` is a no-op and an
advisory lock (pg_advisory_xact_lock) would slot into that same call.

SQLite-only note: the column ensures use ``PRAGMA table_info`` -- needs a
Postgres equivalent later (information_schema).
"""
from __future__ import annotations

from typing import List

from sqlalchemy import inspect

import config
import db
import workflow
from helpers import utc_now_str
from models import Base

LATEST_SCHEMA_VERSION = 19


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

def _get_schema_version(session) -> int:
    row = db.fetch_one(session, "SELECT value FROM app_settings WHERE key = 'schema_version'")
    try:
        return int(row["value"]) if row else 0
    except (TypeError, ValueError):
        return 0


def _set_schema_version(session, version: int) -> None:
    db.execute(session, """
        INSERT INTO app_settings (key, value) VALUES ('schema_version', :version)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, {"version": str(version)})


def _ensure_column(session, table_name: str, column_name: str, column_def: str) -> None:
    # PRAGMA table_info is SQLite-only; table/column names are internal constants.
    cols = [r["name"] for r in db.fetch_all(session, f"PRAGMA table_info({table_name})")]
    if column_name not in cols:
        db.execute(session, f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")


def _ensure_columns(session) -> None:
    """Add columns that legacy databases may be missing (no-op on a fresh DB)."""
    _ensure_column(session, "project_tasks", "backup_owner", "TEXT")
    _ensure_column(session, "project_tasks", "approver", "TEXT")
    _ensure_column(session, "project_tasks", "output_notes", "TEXT")
    _ensure_column(session, "project_tasks", "comments", "TEXT")
    _ensure_column(session, "project_tasks", "priority", "TEXT NOT NULL DEFAULT 'Normal'")
    _ensure_column(session, "project_tasks", "business_plan_enabled", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(session, "project_tasks", "business_plan_year", "INTEGER")
    _ensure_column(session, "project_tasks", "last_updated", "TEXT")
    _ensure_column(session, "projects", "location", "TEXT")
    _ensure_column(session, "projects", "business_plan_enabled", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(session, "projects", "business_plan_year", "INTEGER")
    _ensure_column(session, "projects", "active_well_enabled", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(session, "projects", "pipeline_type", "TEXT NOT NULL DEFAULT 'prospect'")
    _ensure_column(session, "projects", "current_stage_started_at", "TEXT")
    _ensure_column(session, "projects", "last_updated", "TEXT")
    _ensure_column(session, "projects", "archived", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(session, "projects", "lead_folder_path", "TEXT")
    _ensure_column(session, "projects", "lead_x", "INTEGER")
    _ensure_column(session, "projects", "lead_y", "INTEGER")
    _ensure_column(session, "project_overview", "derisking", "TEXT")
    _ensure_column(session, "project_overview", "ogip", "TEXT")
    _ensure_column(session, "project_overview", "lead_ogip", "TEXT")
    _ensure_column(session, "project_overview", "preliminary_resource_estimation", "TEXT")
    _ensure_column(session, "project_overview", "pre_drill_estimation", "TEXT")
    _ensure_column(session, "project_overview", "post_drill_estimation", "TEXT")
    _ensure_column(session, "project_overview", "reservoir_pressure", "TEXT")
    _ensure_column(session, "project_overview", "reservoir_gradient", "TEXT")
    _ensure_column(session, "project_overview", "flowback_results", "TEXT")
    _ensure_column(session, "project_overview", "pay", "TEXT")
    _ensure_column(session, "project_overview", "porosity", "TEXT")
    _ensure_column(session, "project_overview", "swt", "TEXT")
    _ensure_column(session, "project_overview", "quick_look_pay", "TEXT")
    _ensure_column(session, "project_overview", "quick_look_porosity", "TEXT")
    _ensure_column(session, "project_overview", "quick_look_swt", "TEXT")
    _ensure_column(session, "project_overview", "classification", "TEXT")
    _ensure_column(session, "project_overview", "last_updated", "TEXT")
    _ensure_column(session, "projects", "revision", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(session, "project_tasks", "revision", "INTEGER NOT NULL DEFAULT 0")


def _ensure_base_data(session) -> None:
    """Idempotent base-data fixups that ran on every legacy bootstrap."""
    db.execute(session, """
        UPDATE project_overview
        SET lead_ogip = COALESCE(NULLIF(lead_ogip, ''), NULLIF(preliminary_resource_estimation, ''), NULLIF(ogip, ''))
        WHERE COALESCE(lead_ogip, '') = ''
          AND (COALESCE(preliminary_resource_estimation, '') != '' OR COALESCE(ogip, '') != '')
    """)
    db.execute(session, """
        INSERT OR IGNORE INTO business_plan_commitment (commitment_id, last_updated)
        VALUES (1, :now)
    """, {"now": utc_now_str()})
    # Seed the login users (fresh AND existing databases -- this function runs
    # on every bootstrap). INSERT OR IGNORE keys on the UNIQUE name, so reruns
    # and manual role edits in the DB are never clobbered.
    for name, role in config.SEED_USERS:
        db.execute(session, """
            INSERT OR IGNORE INTO users (name, role, created_at)
            VALUES (:name, :role, :now)
        """, {"name": name, "role": role, "now": utc_now_str()})


# ---------------------------------------------------------------------------
# Legacy status / pipeline normalization (ported from the old Database class)
# ---------------------------------------------------------------------------

def _normalize_legacy_statuses(session) -> None:
    legacy_map = {
        'Not Started': 'Not Assigned',
        'Ready': 'In Progress',
        'Waiting': 'Under Review',
        'Complete': 'Approved',
        'Done': 'Approved',
        'Completed': 'Approved',
    }
    for old, new in legacy_map.items():
        db.execute(session, "UPDATE project_tasks SET status = :new WHERE status = :old",
                   {"new": new, "old": old})
        db.execute(session, "UPDATE task_history SET old_status = :new WHERE old_status = :old",
                   {"new": new, "old": old})
        db.execute(session, "UPDATE task_history SET new_status = :new WHERE new_status = :old",
                   {"new": new, "old": old})
    db.execute(session, "UPDATE projects SET overall_status = 'In Progress' WHERE overall_status IN ('Ready', 'Waiting')")
    db.execute(session, "UPDATE projects SET overall_status = 'Completed' WHERE overall_status IN ('Approved')")


def _normalize_pipeline_types(session) -> None:
    """Separate workflow placement from the Business Plan reporting flag.

    Legacy BP-created wells (whose entire Prospect task set was initialized as
    Not Applicable) are re-tagged pipeline_type = 'bp'.
    """
    projects = db.fetch_all(session, "SELECT project_id, pipeline_type, business_plan_enabled FROM projects")
    for project in projects:
        pipeline = str(project["pipeline_type"] or "").strip().lower()
        if pipeline not in {"prospect", "bp"}:
            pipeline = "prospect"
        if pipeline == "prospect" and int(project["business_plan_enabled"] or 0) == 1:
            counts = db.fetch_one(session, """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'Not Applicable' THEN 1 ELSE 0 END) AS not_applicable
                FROM project_tasks
                WHERE project_id = :project_id AND stage_group IN :stages
            """, {"project_id": project["project_id"], "stages": workflow.PROSPECT_STAGES})
            if counts and int(counts["total"] or 0) > 0 and int(counts["total"] or 0) == int(counts["not_applicable"] or 0):
                pipeline = "bp"
        db.execute(session, "UPDATE projects SET pipeline_type = :pipeline WHERE project_id = :project_id",
                   {"pipeline": pipeline, "project_id": project["project_id"]})


# ---------------------------------------------------------------------------
# Numbered migration steps
# ---------------------------------------------------------------------------

def _consolidate_to_v15(session) -> None:
    """Bring an existing database to schema v15 (ports apply_workflow_updates).

    Renames live records to canonical names, upserts templates, deactivates
    retired tasks, backfills genuinely-missing active tasks, normalizes legacy
    statuses/pipeline types, and recalculates Presence CoS -- all without
    deleting historic task records.
    """
    now = utc_now_str()
    # Rename live records in place, preserving task_id, dynamic values and history.
    for legacy_name, canonical_name in workflow.WORKFLOW_TASK_RENAMES.items():
        template = db.fetch_one(session, "SELECT template_id FROM task_templates WHERE task_name = :name",
                                {"name": legacy_name})
        current = db.fetch_one(session, "SELECT template_id FROM task_templates WHERE task_name = :name",
                               {"name": canonical_name})
        if template and not current:
            db.execute(session, "UPDATE task_templates SET task_name = :name WHERE template_id = :template_id",
                       {"name": canonical_name, "template_id": template["template_id"]})
        elif template and current:
            # Keep the legacy template as an archived definition rather than deleting it.
            db.execute(session, "UPDATE task_templates SET task_name = :name WHERE template_id = :template_id",
                       {"name": legacy_name + " (Legacy)", "template_id": template["template_id"]})

        conflicts = db.fetch_all(session, """
            SELECT project_id FROM project_tasks
            WHERE task_name = :legacy_name
              AND project_id IN (SELECT project_id FROM project_tasks WHERE task_name = :canonical_name)
        """, {"legacy_name": legacy_name, "canonical_name": canonical_name})
        conflict_ids = {row["project_id"] for row in conflicts}
        if conflict_ids:
            db.execute(session, """
                UPDATE project_tasks SET is_active = 0, last_updated = :now
                WHERE task_name = :legacy_name AND project_id IN :project_ids
            """, {"now": now, "legacy_name": legacy_name, "project_ids": list(conflict_ids)})
        db.execute(session, """
            UPDATE project_tasks
            SET task_name = :canonical_name, last_updated = COALESCE(last_updated, :now)
            WHERE task_name = :legacy_name AND is_active = 1
        """, {"canonical_name": canonical_name, "now": now, "legacy_name": legacy_name})

    desired_names = {tpl[1] for tpl in workflow.PIPELINE_TEMPLATES}
    template_map = {}
    for sequence_no, tpl in enumerate(workflow.PIPELINE_TEMPLATES, start=1):
        preferred_id, task_name, stage_group, default_role, duration, _depends_on, _branch_type, output = tpl
        existing = db.fetch_one(session, "SELECT template_id FROM task_templates WHERE task_name = :name",
                                {"name": task_name})
        if existing:
            template_id = existing["template_id"]
            db.execute(session, """
                UPDATE task_templates
                SET sequence_no = :sequence_no, stage_group = :stage_group, default_role = :default_role,
                    default_duration_days = :duration, depends_on_template_id = NULL,
                    branch_type = 'normal', mandatory_output = :output
                WHERE template_id = :template_id
            """, {"sequence_no": sequence_no, "stage_group": stage_group, "default_role": default_role,
                  "duration": duration, "output": output, "template_id": template_id})
        else:
            occupied = db.fetch_one(session, "SELECT 1 FROM task_templates WHERE template_id = :template_id",
                                    {"template_id": preferred_id})
            if occupied:
                template_id = db.fetch_one(session,
                                           "SELECT COALESCE(MAX(template_id), 0) + 1 AS next_id FROM task_templates")["next_id"]
            else:
                template_id = preferred_id
            db.execute(session, """
                INSERT INTO task_templates (template_id, sequence_no, task_name, stage_group, default_role,
                                            default_duration_days, depends_on_template_id, branch_type, mandatory_output)
                VALUES (:template_id, :sequence_no, :task_name, :stage_group, :default_role,
                        :duration, NULL, 'normal', :output)
            """, {"template_id": template_id, "sequence_no": sequence_no, "task_name": task_name,
                  "stage_group": stage_group, "default_role": default_role, "duration": duration,
                  "output": output})
        template_map[task_name] = (template_id, sequence_no, stage_group, default_role, duration, output)
        db.execute(session, """
            UPDATE project_tasks
            SET template_id = :template_id, sequence_no = :sequence_no, stage_group = :stage_group,
                is_active = 1, last_updated = COALESCE(last_updated, :now)
            WHERE task_name = :task_name
        """, {"template_id": template_id, "sequence_no": sequence_no, "stage_group": stage_group,
              "now": now, "task_name": task_name})

    # Templates not in the current workflow remain discoverable but are no longer active.
    if desired_names:
        db.execute(session, "UPDATE project_tasks SET is_active = 0 WHERE task_name NOT IN :task_names",
                   {"task_names": list(desired_names)})

    # Backfill only genuinely missing active tasks. Historic inputs are never overwritten.
    projects = db.fetch_all(session, "SELECT project_id, pipeline_type FROM projects")
    for project in projects:
        project_id = project["project_id"]
        existing_names = {r["task_name"] for r in db.fetch_all(session,
            "SELECT task_name FROM project_tasks WHERE project_id = :project_id",
            {"project_id": project_id})}
        pipeline = str(project["pipeline_type"] or "prospect").lower()
        for task_name, values in template_map.items():
            if task_name in existing_names:
                continue
            template_id, sequence_no, stage_group, _role, duration, output = values
            initial_status = "Not Applicable" if pipeline == "bp" and stage_group in workflow.PROSPECT_STAGES else "Not Assigned"
            db.execute(session, """
                INSERT INTO project_tasks (
                    project_id, template_id, sequence_no, task_name, stage_group, status,
                    planned_start, planned_finish, output_notes, priority, is_active, last_updated
                ) VALUES (:project_id, :template_id, :sequence_no, :task_name, :stage_group, :status,
                          NULL, NULL, :output, 'Medium', 1, :now)
            """, {"project_id": project_id, "template_id": template_id, "sequence_no": sequence_no,
                  "task_name": task_name, "stage_group": stage_group, "status": initial_status,
                  "output": output, "now": now})

    _normalize_legacy_statuses(session)
    _normalize_pipeline_types(session)
    # v15: migrate existing Presence CoS values to the automatic Reservoir x Trap x Seal calculation.
    for project in projects:
        workflow.recalculate_presence_cos(session, project["project_id"], "System Migration")


def _upgrade_to_v16(session) -> None:
    """Schema v16: honest completion dates + guaranteed overview rows.

    What it changes and why:

    1. ``projects.completed_at`` (TEXT, nullable) is added. Before v16 the
       "completed wells per month" report bucketed by ``last_updated``, so any
       later edit to a completed well silently moved it to a different month.
       From v16, ``refresh_project_state`` stamps ``completed_at`` exactly when
       ``overall_status`` transitions to 'Completed' (and clears it if the
       project reopens), and reporting buckets by that stamp.
       Backfill: rows already Completed adopt their ``last_updated`` as the
       best available completion timestamp.

    2. Every project is guaranteed a ``project_overview`` row. Before v16 the
       GET endpoints lazily INSERTed missing rows (a write on a read path).
       After this backfill, ``get_project_overview`` /
       ``get_business_plan_commitment`` are pure reads.

    Idempotent by construction: the column add is guarded by ``_ensure_column``,
    the completed_at backfill only touches NULL values, and the overview
    backfill uses INSERT OR IGNORE. This function is the template for future
    numbered steps: one focused change set, a docstring saying what/why, only
    idempotent statements.
    """
    now = utc_now_str()
    _ensure_column(session, "projects", "completed_at", "TEXT")
    db.execute(session, """
        UPDATE projects
        SET completed_at = last_updated
        WHERE overall_status = 'Completed' AND completed_at IS NULL
    """)
    db.execute(session, """
        INSERT OR IGNORE INTO project_overview (project_id, last_updated)
        SELECT project_id, :now FROM projects
    """, {"now": now})


def _upgrade_to_v17(session) -> None:
    """Schema v17: status-model collapse + project-state re-anchor repair.

    Step 1 -- status collapse. The 9-status vocabulary is replaced by the
    4-state implicit lifecycle (Not Assigned / In Progress / Ready / Approved,
    plus internal-only Not Applicable). Mapping for ``project_tasks.status``:

    - 'Assigned'            -> 'In Progress' when an assignee exists,
                               otherwise 'Not Assigned'
    - 'Ready for Review',
      'Under Review',
      'Ready for Approval'  -> 'Ready'
    - 'Returned for Update' -> 'In Progress'
    - 'Not Assigned' / 'In Progress' / 'Approved' / 'Not Applicable' unchanged

    ``task_history.old_status``/``new_status`` follow the same table except
    'Assigned' -> 'In Progress' unconditionally: history rows carry no assignee
    context, and they are audit labels rather than live state.

    Step 2 -- state repair. Re-runs ``refresh_project_state`` for every project
    AFTER the collapse (order matters: refresh evaluates the new vocabulary).
    This also repairs the pre-v17 completion-fallback bug that stamped prospect
    leads with the BP-only "PDA" / "Post-Testing" anchor.

    Idempotent: none of the source statuses survive a first run, and
    refresh_project_state is a pure recompute of derived state. Append future
    idempotent steps below the repair loop; keep each step self-contained.
    """
    # -- Step 1: collapse legacy statuses (style: _normalize_legacy_statuses).
    db.execute(session, """
        UPDATE project_tasks SET status = 'In Progress'
        WHERE status = 'Assigned' AND COALESCE(TRIM(assigned_to), '') != ''
    """)
    db.execute(session, "UPDATE project_tasks SET status = 'Not Assigned' WHERE status = 'Assigned'")
    collapse_map = {
        'Ready for Review': 'Ready',
        'Under Review': 'Ready',
        'Ready for Approval': 'Ready',
        'Returned for Update': 'In Progress',
    }
    for old, new in collapse_map.items():
        db.execute(session, "UPDATE project_tasks SET status = :new WHERE status = :old",
                   {"new": new, "old": old})
    history_map = dict(collapse_map)
    history_map['Assigned'] = 'In Progress'  # audit label; no assignee context on history rows
    for old, new in history_map.items():
        db.execute(session, "UPDATE task_history SET old_status = :new WHERE old_status = :old",
                   {"new": new, "old": old})
        db.execute(session, "UPDATE task_history SET new_status = :new WHERE new_status = :old",
                   {"new": new, "old": old})

    # -- Step 2: re-anchor derived project state under the new vocabulary.
    projects = db.fetch_all(session, "SELECT project_id FROM projects")
    for project in projects:
        workflow.refresh_project_state(session, project["project_id"])


def _upgrade_to_v18(session) -> None:
    """Schema v18: retire the "Presence CoS Evaluation" step; renumber to 1-31.

    Presence CoS is derived (final Reservoir x Trap x Seal), not user-entered,
    so it is removed as a visible workflow step. What this migration does:

    1. Deactivates every "Presence CoS Evaluation" project_tasks row
       (``is_active = 0``). The rows -- and their dynamic fields and history --
       are preserved, never deleted.
    2. Parks the retired template at ``sequence_no`` 999. Its task_templates
       row must survive because the deactivated task rows still reference its
       template_id.
    3. Resequences task_templates AND project_tasks rows to the new contiguous
       1-31 numbering, keyed by task_name against the current
       workflow.PIPELINE_TEMPLATES (the name-keyed loop shape of
       ``_consolidate_to_v15``). template_id primary keys are never changed;
       project_tasks.template_id is re-pointed by name for consistency.
    4. Re-runs ``refresh_project_state`` for every project, fixing any
       ``current_task`` that pointed at the removed step.

    Idempotent: every UPDATE converges (the deactivation, the 999 park, the
    name-keyed resequence and the state refresh all produce the same result on
    a second run). The computed Presence value lives on as
    ``project_overview.derisking``, maintained by recalculate_presence_cos.
    """
    now = utc_now_str()
    db.execute(session, """
        UPDATE project_tasks SET is_active = 0, last_updated = :now
        WHERE task_name = 'Presence CoS Evaluation' AND is_active = 1
    """, {"now": now})
    db.execute(session, """
        UPDATE task_templates SET sequence_no = 999
        WHERE task_name = 'Presence CoS Evaluation'
    """)
    for sequence_no, tpl in enumerate(workflow.PIPELINE_TEMPLATES, start=1):
        task_name = tpl[1]
        template = db.fetch_one(session,
                                "SELECT template_id FROM task_templates WHERE task_name = :name",
                                {"name": task_name})
        if not template:
            # A v17 database always carries these templates (seeded/consolidated
            # earlier); nothing to renumber otherwise.
            continue
        db.execute(session, """
            UPDATE task_templates SET sequence_no = :sequence_no
            WHERE template_id = :template_id
        """, {"sequence_no": sequence_no, "template_id": template["template_id"]})
        db.execute(session, """
            UPDATE project_tasks
            SET sequence_no = :sequence_no, template_id = :template_id, last_updated = COALESCE(last_updated, :now)
            WHERE task_name = :task_name
        """, {"sequence_no": sequence_no, "template_id": template["template_id"],
              "now": now, "task_name": task_name})

    projects = db.fetch_all(session, "SELECT project_id FROM projects")
    for project in projects:
        workflow.refresh_project_state(session, project["project_id"])


# Legacy per-SARH task field key -> project_formations column, per phase.
# These are the pre-WS6 'Quicklook Logs Interpretation' / 'Final Log Analysis'
# dynamic-field keys (verified against the pre-WS6 schema.js).
_V19_LEGACY_FORMATION_KEYS = {
    "quicklook": {
        "top_tvdss_ft": "quicklook_top_sarah_tvdss_ft",
        "base_tvdss_ft": "quicklook_base_sarah_tvdss_ft",
        "thickness_ft": "quicklook_formation_thickness_ft",
        "porosity_pct": "quicklook_average_porosity_pct",
        "swt_pct": "quicklook_average_swt_pct",
        "pay_ft": "quicklook_pay_thickness_ft",
        "ngr_pct": "quicklook_ngr_pct",
        "fluid": "quicklook_fluid_type",
    },
    "final": {
        "top_tvdss_ft": "final_top_sarah_tvdss_ft",
        "base_tvdss_ft": "final_base_sarah_tvdss_ft",
        "thickness_ft": "final_formation_thickness_ft",
        "porosity_pct": "final_average_porosity_pct",
        "swt_pct": "final_average_swt_pct",
        "pay_ft": "final_pay_thickness_ft",
        "ngr_pct": "final_ngr_pct",
        "fluid": "final_fluid_type",
    },
}

_V19_PHASE_TASK_NAMES = {
    # "Quicklook Logs" is the pre-v15 legacy task name; some databases still
    # carry deactivated rows under it with field data.
    "quicklook": ["Quicklook Logs Interpretation", "Quicklook Logs"],
    "final": ["Final Log Analysis"],
}


def _upgrade_to_v19(session) -> None:
    """Schema v19 (first slice): backfill SARH formation rows from legacy fields.

    WS6 moved formation interpretation values off the quicklook/final steps'
    scattered task dynamic fields into the well-level ``project_formations``
    table (created by create_all -- no DDL needed here). This step seeds a SARH
    row per phase from the legacy field keys whenever any legacy value exists.

    INSERT OR IGNORE keys off UNIQUE(project_id, formation, phase), so the step
    is idempotent and never overwrites a row the user has since edited. The
    legacy task fields themselves are left untouched (historical record).

    NOTE (WS7, second v19 slice): the Portfolio rework needed only one new
    column -- project_overview.classification -- which is added by
    ``_ensure_columns`` (empty column, no data transform), so no additional
    numbered step lives here. Append any future idempotent v19 steps below
    this backfill; keep each self-contained.
    """
    now = utc_now_str()
    projects = db.fetch_all(session, "SELECT project_id FROM projects")
    for project in projects:
        project_id = project["project_id"]
        for phase, key_map in _V19_LEGACY_FORMATION_KEYS.items():
            legacy_keys = list(key_map.values())
            rows = db.fetch_all(session, """
                SELECT tdf.field_key, tdf.field_value
                FROM project_tasks pt
                JOIN task_dynamic_fields tdf ON tdf.task_id = pt.task_id
                WHERE pt.project_id = :project_id AND pt.task_name IN :task_names
                  AND tdf.field_key IN :field_keys
                ORDER BY pt.is_active, pt.task_id
            """, {"project_id": project_id,
                  "task_names": _V19_PHASE_TASK_NAMES[phase],
                  "field_keys": legacy_keys})
            # Later rows win in this dict; the ORDER BY puts active tasks last
            # so their values take precedence over deactivated legacy rows.
            found = {row["field_key"]: (row["field_value"] or "").strip() for row in rows}
            if not any(found.get(key) for key in legacy_keys):
                continue
            params = {column: found.get(legacy_key, "")
                      for column, legacy_key in key_map.items()}
            params.update({"project_id": project_id, "phase": phase, "now": now})
            db.execute(session, """
                INSERT OR IGNORE INTO project_formations (
                    project_id, formation, phase, top_tvdss_ft, base_tvdss_ft, thickness_ft,
                    porosity_pct, swt_pct, pay_ft, ngr_pct, fluid, updated_at, updated_by
                ) VALUES (:project_id, 'SARH', :phase, :top_tvdss_ft, :base_tvdss_ft, :thickness_ft,
                          :porosity_pct, :swt_pct, :pay_ft, :ngr_pct, :fluid, :now, 'System Migration')
            """, params)


# List of (version, fn). Each step upgrades a database from below ``version`` to
# ``version``. Add new steps here with the next integer version.
MIGRATIONS: List = [
    (15, _consolidate_to_v15),
    (16, _upgrade_to_v16),
    (17, _upgrade_to_v17),
    (18, _upgrade_to_v18),
    (19, _upgrade_to_v19),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(session, engine) -> None:
    """Create/upgrade the schema for the given engine.

    The ensure/seed block and each numbered migration step run in their own
    transaction, each opened with an upfront write lock (db.begin_write) so
    concurrent bootstrapping processes serialize instead of interleaving.
    """
    # Detect fresh-vs-existing on the engine (its own connection) BEFORE the
    # session opens a transaction, so create_all's DDL never contends with a
    # session-held SQLite lock. inspect(...) is dialect-portable.
    fresh = "projects" not in inspect(engine).get_table_names()
    Base.metadata.create_all(engine)

    # Upfront write lock for the ensure/seed transaction (SQLite BEGIN IMMEDIATE;
    # a Postgres advisory lock would slot into begin_write).
    db.begin_write(session)
    _ensure_columns(session)
    _ensure_base_data(session)
    workflow.seed_templates(session)

    if fresh:
        # An empty database jumps straight to the latest version; the historical
        # steps would only be no-ops against empty data.
        _set_schema_version(session, LATEST_SCHEMA_VERSION)
        session.commit()
        return

    current = _get_schema_version(session)
    session.commit()
    for version, migrate in MIGRATIONS:
        if version > current:
            db.begin_write(session)  # one locked transaction per migration step
            migrate(session)
            _set_schema_version(session, version)
            session.commit()
