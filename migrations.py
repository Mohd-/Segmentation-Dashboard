"""Schema bootstrap and in-place, numbered migrations.

What belongs here:
- Creating a fresh schema (via ``models.Base.metadata.create_all``), seeding
  base data (users, the commitment row), and stamping ``schema_version``.
- Append-only ``MIGRATIONS`` steps that upgrade an EXISTING database in place
  to the current models.py shape.

What does NOT belong here:
- Runtime domain logic (workflow.py). The workflow definition itself lives in
  code (``workflow.PIPELINE_TEMPLATES``) -- there is no templates table to
  seed.

Migration policy -- the pre-deployment "throwaway database" era is OVER:
databases now hold real lead/well data that every schema change must carry
forward, never discard.
- A fresh database is still created straight from models.py (``create_all``
  builds the full current shape) and stamped LATEST_SCHEMA_VERSION; migration
  steps never run for it.
- An existing database gets every ``MIGRATIONS`` step newer than its stored
  ``schema_version``, in ascending order, then is stamped current.
  (``create_all`` still runs first: it creates newly-added TABLES for free;
  steps are needed for everything else -- new columns, reshapes, backfills.)
- Steps are APPEND-ONLY and GUARDED-IDEMPOTENT: never edit or remove a
  shipped step (append a new one instead), and guard each step so a database
  already carrying the change (e.g. hand-ALTERed) passes through unchanged.
  Every step lands with an upgrade-and-replay test -- see CONTRIBUTING.md
  recipe 5 and tests/test_bootstrap.py.

Concurrency guard: ``run`` acquires the database write lock UPFRONT via
``db.begin_write`` (SQLite ``BEGIN IMMEDIATE``) before the migrate/ensure/seed
writes, so concurrent processes cannot interleave bootstrap writes. On
Postgres ``begin_write`` is a no-op and an advisory lock
(pg_advisory_xact_lock) would slot into that same call.
"""
from __future__ import annotations

import json

from sqlalchemy import inspect

import config
import db
from helpers import utc_now_str
from models import Base

LATEST_SCHEMA_VERSION = 12


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


def _ensure_base_data(session) -> None:
    """Idempotent base-data seeding that runs on every bootstrap."""
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
# Migration steps (append-only; see the module docstring's policy)
# ---------------------------------------------------------------------------

def _migrate_v2_users_password_hash(session, engine) -> None:
    """v2: add the nullable ``users.password_hash`` column (per-user login
    passwords, written by scripts/add_users.py, checked by POST /api/login).

    Guarded on column existence: a database already ALTERed by hand (the
    documented one-liner) passes through unchanged instead of hitting a
    duplicate-column error.
    """
    columns = {column["name"] for column in inspect(engine).get_columns("users")}
    if "password_hash" not in columns:
        db.execute(session, "ALTER TABLE users ADD COLUMN password_hash TEXT")


def _migrate_v3_rename_quicklook_logs(session, engine) -> None:
    """v3: rename step 18 "Quicklook Logs Interpretation" -> "Quicklook Logs".

    ``project_tasks.task_name`` stores the step name per row, so renaming a
    step in ``workflow.PIPELINE_TEMPLATES`` only affects NEW projects -- every
    existing row must be carried over here, or those wells lose the step's
    identity (its dynamic fields, history and folder card all hang off the
    name).

    Guarded twice:
    - only rows still holding the OLD name are touched, which makes a replay a
      no-op (naturally idempotent);
    - a project that somehow holds BOTH names (a hand-inserted row, or a
      half-applied rename) is SKIPPED rather than updated: ``project_tasks``
      has UNIQUE(project_id, task_name), so renaming there would abort the
      whole bootstrap with an integrity error. The stale old-name row is left
      in place for manual reconciliation; ``workflow.projects``' board query
      reads both names, so nothing disappears from the UI in the meantime.

    ``task_history`` keeps its old ``task_name`` snapshots on purpose: it is an
    append-only record of what the step was called when the event happened, not
    a mirror of the current name.
    """
    db.execute(session, """
        UPDATE project_tasks
        SET task_name = 'Quicklook Logs'
        WHERE task_name = 'Quicklook Logs Interpretation'
          AND project_id NOT IN (
              SELECT project_id FROM project_tasks WHERE task_name = 'Quicklook Logs'
          )
    """)


# v4 merges four BP steps into the three that absorbed them. Both tables below
# are FROZEN COPIES of what workflow.PIPELINE_TEMPLATES looked like at v4 --
# deliberately NOT imported from there, so a later template edit can never
# retroactively change what this shipped step does (the append-only rule).
_V4_RETIRED_TASK_NAMES = (
    "URED Update",                        # -> Executive Summary (checkbox)
    "Post-Drilling Resource Assessment",  # -> SAD Model (same EAV keys)
    "Resource Assessment Update",         # -> SAD Update (same EAV keys)
    "Executive Summary Final",            # -> SAD Update (required checkbox)
)

# task_name -> new sequence_no, for the surviving steps whose number MOVED when
# the four retired steps left the list. Steps 1-19 are unchanged and absent.
_V4_RESEQUENCE = {
    "SAD Model": 20,                        # was 21
    "Executive Summary": 21,                # was 22
    "Post-Well Outcome & Decision Gate": 22,  # was 24
    "Flowback Results": 23,                 # was 25
    "SAD Update": 24,                       # was 26
    "Final Log Analysis": 25,               # was 28
    "PVAD Structural MTR": 26,              # was 29
    "PDA": 27,                              # was 31
}


def _migrate_v4_bp_step_merges(session, engine) -> None:
    """v4: merge four BP steps away and renumber the survivors (31 -> 27).

    "URED Update" folds into "Executive Summary", "Post-Drilling Resource
    Assessment" into "SAD Model", and "Resource Assessment Update" +
    "Executive Summary Final" into "SAD Update".

    NOTHING IS DELETED. Retiring a step is ``project_tasks.is_active = 0``:
    the row, its ``task_dynamic_fields`` and its ``task_history`` all survive,
    and the merged step reuses the retired step's EAV keys (post_drill_piip_*,
    resource_update_*) so no stored value is orphaned. The readers are
    retired-inclusive (workflow.summary.get_project_dynamic_field_map,
    reporting._bp_task_fields, portfolio_export._task_fields), which is what
    makes a well drilled before the merge keep showing its numbers -- with the
    surviving step's bucket winning wherever both are filled.

    The resequence keeps ``sequence_no`` (the number the UI prints next to a
    component) meaning the same thing on old and new records. It is a plain
    per-name UPDATE: ``project_tasks`` has UNIQUE(project_id, task_name) but no
    uniqueness on sequence_no, so there is no transient-collision hazard, and
    the retired rows keep their old numbers (they are excluded from every
    ordered read by ``is_active = 1``).

    Guarded/idempotent both ways: the deactivation only touches rows still
    active under a retired name, and each resequence only touches rows not
    already carrying the new number, so a replay is a no-op.
    """
    db.execute(session, """
        UPDATE project_tasks
        SET is_active = 0
        WHERE is_active != 0 AND task_name IN :retired
    """, {"retired": list(_V4_RETIRED_TASK_NAMES)})
    for task_name, sequence_no in _V4_RESEQUENCE.items():
        db.execute(session, """
            UPDATE project_tasks
            SET sequence_no = :sequence_no
            WHERE task_name = :task_name AND sequence_no != :sequence_no
        """, {"sequence_no": sequence_no, "task_name": task_name})


# ---------------------------------------------------------------------------
# v5: the permanent 12-tracked-item PROSPECT template
# ---------------------------------------------------------------------------
# Every table below is a FROZEN COPY of the v5 shape, deliberately NOT imported
# from workflow.constants, so a later template edit can never retroactively
# change what this shipped step does (the append-only rule -- same discipline as
# the v4 tables above).
#
# Scope note: this touches EVERY project, not only pipeline_type = 'prospect'.
# add_project materializes all PIPELINE_TEMPLATES rows for every record
# regardless of pipeline (applicability is derived per read, never stored), so a
# promoted BP well carries the same twelve prospect rows and its lead phase must
# be restructured identically -- otherwise a recall would surface a half-migrated
# lead. Nothing here reads or writes projects.pipeline_type.

# (a) RENAMES -- pre-v5 name -> v5 name, applied in place (v3 pattern).
_V5_RENAMES = (
    ("Reservoir Area Definition", "Area Definition"),
    ("Lead Resource Assessment", "Resource Assessment"),
    ("Prospect Evaluation Presentation", "Segmentation Slides"),
    ("Staking Moving Tolerance", "Moving Tolerance"),
    ("Pre-Drilling Resource Assessment", "Pre-Drilling GeoX Assessment"),
    # "Thickness Estimation" and "Approval to Stake" keep their names.
)

# (e) STAGE GROUPS -- pre-v5 prospect group -> v5 group. BP groups are absent
# on purpose and are never touched. Applied to ALL prospect rows (active and
# retired alike) so the stored vocabulary is uniform afterwards.
_V5_STAGE_GROUPS = (
    ("Lead Identification", "Lead Assessment"),
    ("Risking", "Risk Analysis"),
    ("Segmentation", "Risk Analysis"),
    # "Pre-Well Delivery" is unchanged.
)

# (b) MERGE. The lifecycle order that decides the merged row's status: the LESS
# advanced of the two halves wins, which by construction yields Approved only
# when BOTH halves are Approved.
_V5_LIFECYCLE_RANK = {"Not Assigned": 0, "In Progress": 1, "Ready": 2, "Approved": 3}
_V5_MERGED_COS = "Trap and Seal CoS"
_V5_MERGED_COS_SOURCES = ("Trap CoS", "Seal CoS")   # precedence order for assignee/priority
_V5_MERGED_COS_SEQUENCE = 6
_V5_MERGED_COS_STAGE = "Risk Analysis"
# Priority is a per-task field with no lifecycle meaning; the merged row adopts
# the MORE urgent of the two halves so a raised flag is never quietly dropped.
_V5_PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}

# (c) The Well Creation retirement and the checkbox that replaces it.
_V5_RETIRED_WELL_CREATION = "Well Creation"
_V5_STAKING_STEP = "Approval to Stake"
_V5_STAKING_WELL_CREATED_KEY = "staking_well_created"

# (d) The two brand-new tracked items: (task_name, sequence_no, stage_group).
_V5_NEW_STEPS = (
    ("GRV Inputs", 3, "Lead Assessment"),
    ("Well Site Location", 11, "Pre-Well Delivery"),
)
# Card-29 constraint 2 ("preserve their current stage"): an inserted row is
# backfilled ALREADY Approved when ITS OWN stage group was already fully
# approved, with no actual dates (nobody did the work on a date) and this event
# on the trail.
_V5_BACKFILL_EVENT = "Migration-Completed"
_V5_BACKFILL_COMMENT = "Migration-completed (backfilled tracked item on an already-completed lead)"
_V5_MIGRATION_ACTOR = "System (migration v5)"

# (f) RESEQUENCE -- the frozen 1-12 map for the surviving prospect steps (v4
# pattern). BP steps keep 13-27: the prospect half still has twelve steps, so
# no BP number moved.
_V5_RESEQUENCE = {
    "Area Definition": 1,
    "Thickness Estimation": 2,
    "GRV Inputs": 3,
    "Resource Assessment": 4,
    "Reservoir CoS": 5,
    "Trap and Seal CoS": 6,
    "Seismic Signature Validation": 7,
    "Segmentation Slides": 8,
    "Moving Tolerance": 9,
    "Approval to Stake": 10,
    "Well Site Location": 11,
    "Pre-Drilling GeoX Assessment": 12,
}


def _v5_log(session, task_id, project_id, task_name, action_type, comment,
            old_status=None, new_status=None):
    """Append one task_history row, raw.

    Deliberately NOT workflow.history.log_task_event: a shipped migration step
    must not change behaviour when the runtime helper it borrowed is edited.
    """
    db.execute(session, """
        INSERT INTO task_history (task_id, project_id, task_name, action_type,
                                  old_status, new_status, changed_at, changed_by, comment)
        VALUES (:task_id, :project_id, :task_name, :action_type,
                :old_status, :new_status, :changed_at, :changed_by, :comment)
    """, {"task_id": task_id, "project_id": project_id, "task_name": task_name,
          "action_type": action_type, "old_status": old_status, "new_status": new_status,
          "changed_at": utc_now_str(), "changed_by": _V5_MIGRATION_ACTOR, "comment": comment})


def _v5_rename_steps(session) -> None:
    """(a) Rename the five carried-over prospect steps IN PLACE.

    Same double guard as the v3 quicklook rename: only rows still holding the
    OLD name are touched (so a replay is a no-op), and a project that somehow
    holds BOTH names is SKIPPED rather than updated -- project_tasks has
    UNIQUE(project_id, task_name), so renaming there would abort the whole
    bootstrap. Renaming in place is the whole point: the row keeps its task_id,
    so its dynamic fields, history, formation links and share folder follow it.
    """
    for old_name, new_name in _V5_RENAMES:
        db.execute(session, """
            UPDATE project_tasks
            SET task_name = :new_name
            WHERE task_name = :old_name
              AND project_id NOT IN (
                  SELECT project_id FROM project_tasks WHERE task_name = :new_name
              )
        """, {"old_name": old_name, "new_name": new_name})


def _v5_remap_stage_groups(session) -> None:
    """(e) Fold the four stored prospect stage groups into the three v5 ones.

    Runs EARLY (right after the renames) rather than last, so every later
    sub-step -- the merged row's own stage_group, the "was this lead already
    complete?" test, the inserted rows -- speaks one vocabulary instead of
    straddling both. Naturally idempotent: after the update no row holds the old
    value. Retired rows are remapped too (mapping-all), so nothing is left
    filed under a group name the code no longer knows.
    """
    for old_group, new_group in _V5_STAGE_GROUPS:
        db.execute(session, "UPDATE project_tasks SET stage_group = :new_group "
                            "WHERE stage_group = :old_group",
                   {"old_group": old_group, "new_group": new_group})


def _v5_merge_trap_and_seal(session, now) -> None:
    """(b) Fold "Trap CoS" + "Seal CoS" into one new "Trap and Seal CoS" row.

    Per project holding at least one half and not already holding the merged
    name (the idempotence guard):
    - status   = the LESS advanced half by lifecycle order, which yields
                 Approved only when BOTH halves are Approved -- a merged step
                 cannot read as finished while half of its form is outstanding;
    - assignee = Trap's, else Seal's (a single owner; the other half's name
                 survives on its own retired row and in the history);
    - priority = the MORE urgent of the two;
    - dates    = earliest actual_start of the two; actual_finish only when the
                 merged status is Approved, and then the LATEST of the two (the
                 date the pair actually finished).

    The two halves are then retired (is_active = 0, v4 pattern): rows, EAV and
    history all survive.

    EAV: the halves' stored values are COPIED onto the merged row (never moved,
    never deleted). The merged form is a task-scoped read -- GET /api/tasks/<id>
    /dynamic-fields -- so without the copy every pre-v5 lead would open a BLANK
    Trap and Seal form and its Trap/Seal inputs would be invisible on the page
    they now belong to. The legacy buckets stay put underneath and every reader
    keeps a surviving-first ladder over them (constants.TRAP_COS_SOURCES /
    SEAL_COS_SOURCES), so the copy is additive belt-and-braces, not a move.
    """
    rows = db.fetch_all(session, """
        SELECT task_id, project_id, task_name, status, assigned_to, priority,
               actual_start, actual_finish, business_plan_enabled, business_plan_year
        FROM project_tasks
        WHERE task_name IN :sources
          AND project_id NOT IN (
              SELECT project_id FROM project_tasks WHERE task_name = :merged
          )
        ORDER BY project_id, task_id
    """, {"sources": list(_V5_MERGED_COS_SOURCES), "merged": _V5_MERGED_COS})
    halves = {}
    for row in rows:
        # Legacy duplicate rows under one name: the LAST (highest task_id) wins,
        # matching every other fold in the codebase.
        halves.setdefault(row["project_id"], {})[row["task_name"]] = row

    for project_id, by_name in halves.items():
        trap = by_name.get("Trap CoS")
        seal = by_name.get("Seal CoS")
        present = [half for half in (trap, seal) if half]
        status = min((half["status"] or "Not Assigned" for half in present),
                     key=lambda value: _V5_LIFECYCLE_RANK.get(value, 0))
        assigned_to = next((half["assigned_to"] for half in present if half["assigned_to"]), None)
        priority = min((half["priority"] or "Medium" for half in present),
                       key=lambda value: _V5_PRIORITY_RANK.get(value, 1))
        starts = [half["actual_start"] for half in present if half["actual_start"]]
        finishes = [half["actual_finish"] for half in present if half["actual_finish"]]
        anchor = trap or seal
        result = db.execute(session, """
            INSERT INTO project_tasks (
                project_id, sequence_no, task_name, stage_group, assigned_to, status,
                actual_start, actual_finish, comments, priority, business_plan_enabled,
                business_plan_year, is_active, last_updated
            ) VALUES (:project_id, :sequence_no, :task_name, :stage_group, :assigned_to, :status,
                      :actual_start, :actual_finish, NULL, :priority, :business_plan_enabled,
                      :business_plan_year, 1, :now)
        """, {
            "project_id": project_id, "sequence_no": _V5_MERGED_COS_SEQUENCE,
            "task_name": _V5_MERGED_COS, "stage_group": _V5_MERGED_COS_STAGE,
            "assigned_to": assigned_to, "status": status,
            "actual_start": min(starts) if starts else None,
            "actual_finish": (max(finishes) if finishes else None) if status == "Approved" else None,
            "priority": priority,
            "business_plan_enabled": anchor["business_plan_enabled"] or 0,
            "business_plan_year": anchor["business_plan_year"], "now": now,
        })
        merged_task_id = result.lastrowid
        # Copy each half's stored inputs onto the merged row. The two halves
        # share no field key, so there is nothing to arbitrate between them;
        # NOT EXISTS keeps a hand-repaired database from double-inserting.
        for half in present:
            db.execute(session, """
                INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
                SELECT :merged_task_id, source.field_key, source.field_value, :now
                FROM task_dynamic_fields source
                WHERE source.task_id = :source_task_id
                  AND NOT EXISTS (
                      SELECT 1 FROM task_dynamic_fields existing
                      WHERE existing.task_id = :merged_task_id
                        AND existing.field_key = source.field_key
                  )
            """, {"merged_task_id": merged_task_id, "source_task_id": half["task_id"], "now": now})
        _v5_log(session, merged_task_id, project_id, _V5_MERGED_COS, "Migration-Merged",
                "Migration-merged from Trap CoS + Seal CoS; both retired with their inputs "
                "and history intact.", None, status)

    # Retire both halves everywhere (guarded on is_active, so a replay is a
    # no-op even for a project whose merged row already existed).
    db.execute(session, """
        UPDATE project_tasks
        SET is_active = 0
        WHERE is_active != 0 AND task_name IN :sources
    """, {"sources": list(_V5_MERGED_COS_SOURCES)})


def _v5_retire_well_creation(session, now) -> None:
    """(c) Retire "Well Creation"; carry an APPROVED one onto Approval to Stake.

    The step's only real output was "the well record exists", which is now the
    Staking Letters prerequisite checkbox on "Approval to Stake" (Card 4B). A
    project whose Well Creation was Approved therefore gets
    ``staking_well_created = '1'`` written onto its Approval-to-Stake row, as an
    AUDITED insert (a task_history note beside the value, like any user save)
    and guarded on absence so a replay -- or a checkbox a user has since
    unticked -- is never overwritten.
    """
    rows = db.fetch_all(session, """
        SELECT staking.task_id AS task_id, staking.project_id AS project_id
        FROM project_tasks staking
        JOIN project_tasks creation
          ON creation.project_id = staking.project_id
         AND creation.task_name = :creation_name
         AND creation.status = 'Approved'
        WHERE staking.task_name = :staking_name
          AND NOT EXISTS (
              SELECT 1 FROM task_dynamic_fields
              WHERE task_id = staking.task_id AND field_key = :field_key
          )
    """, {"creation_name": _V5_RETIRED_WELL_CREATION, "staking_name": _V5_STAKING_STEP,
          "field_key": _V5_STAKING_WELL_CREATED_KEY})
    for row in rows:
        db.execute(session, """
            INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
            VALUES (:task_id, :field_key, '1', :now)
        """, {"task_id": row["task_id"], "field_key": _V5_STAKING_WELL_CREATED_KEY, "now": now})
        _v5_log(session, row["task_id"], row["project_id"], _V5_STAKING_STEP,
                "Component Inputs Updated",
                "Migration-carried the approved Well Creation sign-off onto this step "
                "(staking well created).")

    db.execute(session, """
        UPDATE project_tasks
        SET is_active = 0
        WHERE is_active != 0 AND task_name = :creation_name
    """, {"creation_name": _V5_RETIRED_WELL_CREATION})


def _v5_insert_new_steps(session, now) -> None:
    """(d) Materialize "GRV Inputs" and "Well Site Location" on every project.

    Card-29 constraint 2 -- an existing lead must PRESERVE ITS CURRENT STAGE. An
    inserted row is therefore backfilled already Approved (NULL actual dates,
    plus a Migration-Completed history event saying why) when ITS OWN STAGE
    GROUP was already fully approved before the insert; otherwise it arrives
    "Not Assigned" like any unstarted step.

    Per-STAGE, not per-lead, is what actually preserves the stage. The derived
    current_stage is the stage of the first non-Approved active row in sequence
    order, so dropping an unstarted row into a stage the lead has already
    FINISHED would drag its board card back a column -- exactly the backward
    move the card forbids. Scoping the test to the row's own stage makes that
    impossible: a stage the lead has passed gets a backfilled row and stays
    passed, while a stage still in flight was already the current stage (or is
    later than it), so adding an unstarted row there cannot move the pointer
    earlier. A wholly completed lead is the special case where every stage
    qualifies, so both rows are backfilled and the lead stays Completed at 100%.

    The stage vocabulary is the v5 one, because (e) has already run. Measured
    AFTER (b) and (c) too, so a half-approved Trap/Seal pair correctly reads as
    unfinished and a merely un-approved (now retired) Well Creation correctly no
    longer holds a stage back. The two inserted rows live in DIFFERENT stage
    groups, so neither can affect the other's measurement whatever the order.

    Guarded on absence per project, so a replay inserts nothing.
    """
    for task_name, sequence_no, stage_group in _V5_NEW_STEPS:
        rows = db.fetch_all(session, """
            SELECT p.project_id AS project_id,
                   (SELECT COUNT(*) FROM project_tasks pt
                     WHERE pt.project_id = p.project_id AND pt.is_active = 1
                       AND pt.stage_group = :stage_group) AS stage_count,
                   (SELECT COUNT(*) FROM project_tasks pt
                     WHERE pt.project_id = p.project_id AND pt.is_active = 1
                       AND pt.stage_group = :stage_group
                       AND pt.status != 'Approved') AS open_count
            FROM projects p
            WHERE p.project_id NOT IN (
                SELECT project_id FROM project_tasks WHERE task_name = :task_name
            )
        """, {"stage_group": stage_group, "task_name": task_name})
        for row in rows:
            complete = int(row["stage_count"] or 0) > 0 and int(row["open_count"] or 0) == 0
            status = "Approved" if complete else "Not Assigned"
            result = db.execute(session, """
                INSERT INTO project_tasks (
                    project_id, sequence_no, task_name, stage_group, assigned_to, status,
                    actual_start, actual_finish, comments, priority, business_plan_enabled,
                    business_plan_year, is_active, last_updated
                ) VALUES (:project_id, :sequence_no, :task_name, :stage_group, NULL, :status,
                          NULL, NULL, NULL, 'Low', 0, NULL, 1, :now)
            """, {"project_id": row["project_id"], "sequence_no": sequence_no,
                  "task_name": task_name, "stage_group": stage_group,
                  "status": status, "now": now})
            if complete:
                _v5_log(session, result.lastrowid, row["project_id"], task_name,
                        _V5_BACKFILL_EVENT, _V5_BACKFILL_COMMENT, None, "Approved")


def _v5_resequence(session) -> None:
    """(f) Renumber the surviving prospect steps to a contiguous 1-12.

    Plain per-name UPDATEs (v4 pattern): project_tasks has no uniqueness on
    sequence_no, so there is no transient-collision hazard, and each statement
    skips rows already carrying the target number, which makes a replay a no-op.
    The retired rows keep their old numbers -- every ordered read filters on
    is_active = 1. BP steps 13-27 are untouched: the prospect half still has
    exactly twelve steps.
    """
    for task_name, sequence_no in _V5_RESEQUENCE.items():
        db.execute(session, """
            UPDATE project_tasks
            SET sequence_no = :sequence_no
            WHERE task_name = :task_name AND sequence_no != :sequence_no
        """, {"sequence_no": sequence_no, "task_name": task_name})


def _migrate_v5_prospect_template_restructure(session, engine) -> None:
    """v5: the permanent 12-tracked-item prospect template.

    The board and the lead detail sidebar had been showing twelve "tracked
    items" over three stages through a read-time presentation adapter, because
    the STORED prospect workflow was a different (four-stage, differently named)
    twelve. This step makes the stored workflow BE the twelve, so the adapter
    can be deleted and every tracked item becomes a real, openable, assignable,
    approvable step with its own inputs, history and folder.

    Ordered sub-steps, each independently guarded and idempotent:
      (a) rename the five carried-over steps in place,
      (e) remap the prospect stage groups (run early, see its docstring),
      (b) merge Trap CoS + Seal CoS into one row and retire both halves,
      (c) retire Well Creation, carrying an approved one onto Approval to Stake,
      (d) insert GRV Inputs + Well Site Location (Approved on an already
          complete lead, Not Assigned otherwise),
      (f) resequence the survivors to 1-12.

    NOTHING IS DELETED anywhere: retiring is ``is_active = 0``, renaming is in
    place, and the merge copies rather than moves. A replay of the whole step
    changes nothing.
    """
    now = utc_now_str()
    _v5_rename_steps(session)
    _v5_remap_stage_groups(session)
    _v5_merge_trap_and_seal(session, now)
    _v5_retire_well_creation(session, now)
    _v5_insert_new_steps(session, now)
    _v5_resequence(session)


def _migrate_v6_ground_elevation(session, engine) -> None:
    """v6: add the nullable ``projects.ground_elevation`` REAL column.

    Machine-derived (the DEM surface sampled at the project's coordinates by
    workflow/surfaces_fill.fill_ground_elevation), so there is nothing to
    backfill here: the column arrives NULL everywhere and is populated by the
    save-time fill / scripts/backfill_surfaces.py, both of which may run any
    number of times.

    Guarded on column existence (the ``_migrate_v2_users_password_hash``
    pattern): a database already ALTERed by hand passes through unchanged
    instead of hitting a duplicate-column error.
    """
    columns = {column["name"] for column in inspect(engine).get_columns("projects")}
    if "ground_elevation" not in columns:
        db.execute(session, "ALTER TABLE projects ADD COLUMN ground_elevation REAL")


# v7: one Lead Assessment lifecycle row, with these four former rows retained
# only as historical audit anchors.  Keep this frozen migration vocabulary here
# rather than importing workflow.constants: a shipped migration must not change
# behavior when a runtime display label later changes.
_V7_LEAD_ASSESSMENT = "Lead Assessment"
_V7_LEGACY_LEAD_STEPS = (
    "Area Definition",
    "Thickness Estimation",
    "GRV Inputs",
    "Resource Assessment",
)
_V7_STATUS_RANK = {
    "Not Assigned": 0,
    "In Progress": 1,
    "Ready": 2,
    "Approved": 3,
}
_V7_PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}
_V7_RESEQUENCE = {
    "Lead Assessment": 1,
    "Reservoir CoS": 2,
    "Trap and Seal CoS": 3,
    "Seismic Signature Validation": 4,
    "Segmentation Slides": 5,
    "Moving Tolerance": 6,
    "Approval to Stake": 7,
    "Well Site Location": 8,
    "Pre-Drilling GeoX Assessment": 9,
}
_V7_MIGRATION_ACTOR = "System (migration v7)"
_V7_MIGRATION_COMMENT = "Merged from 4 Lead Assessment steps (migration v7)"


def _v7_log(session, task_id, project_id, status,
            actor=_V7_MIGRATION_ACTOR, comment=_V7_MIGRATION_COMMENT):
    """Write the one immutable migration event on the v7 survivor.

    ``actor``/``comment`` default to the frozen v7 values so a v7 replay stays
    byte-identical; v8's repair fold passes its own so the audit trail says
    which migration actually performed the merge.
    """
    db.execute(session, """
        INSERT INTO task_history (task_id, project_id, task_name, action_type,
                                  old_status, new_status, changed_at, changed_by, comment)
        VALUES (:task_id, :project_id, :task_name, 'Migration-Merged',
                NULL, :status, :changed_at, :changed_by, :comment)
    """, {"task_id": task_id, "project_id": project_id,
          "task_name": _V7_LEAD_ASSESSMENT, "status": status,
          "changed_at": utc_now_str(), "changed_by": actor,
          "comment": comment})


def _v7_assert_disjoint_field_keys(session, project_id, source_rows):
    """Fail closed before moving EAV rows from the four v7 source tasks.

    The consolidated workspace needs one owner for every value.  Moving a
    duplicate key onto that owner would hit its unique constraint and, worse,
    would force a silent choice between two historical values.  The four v7
    key families are designed to be disjoint; prove that against the ACTUAL
    database before executing any move.  A hand-edited collision is therefore
    a loud, repairable migration failure rather than data loss.
    """
    rows = db.fetch_all(session, """
        SELECT task_id, field_key
        FROM task_dynamic_fields
        WHERE task_id IN :task_ids
        ORDER BY task_id, field_key
    """, {"task_ids": [row["task_id"] for row in source_rows]})
    owner_by_key = {}
    name_by_id = {row["task_id"]: row["task_name"] for row in source_rows}
    for row in rows:
        key = row["field_key"]
        owner = owner_by_key.setdefault(key, row["task_id"])
        if owner != row["task_id"]:
            raise RuntimeError(
                "Migration v7 refused to merge project "
                f"{project_id}: dynamic field key {key!r} appears on both "
                f"{name_by_id[owner]!r} and {name_by_id[row['task_id']]!r}.")


def _v7_merged_comments(source_rows):
    """Preserve v6 task comments on the one v7 lifecycle row.

    A single non-blank comment remains verbatim, so a straightforward legacy
    note does not gain migration noise.  Multiple notes need their source
    labels because the four old comment columns carried distinct provenance;
    source_rows are already in frozen task order, making this output stable on
    every run and easy to read in the consolidated workspace.
    """
    comments = [(row["task_name"], row["comments"])
                for row in source_rows if str(row.get("comments") or "").strip()]
    if not comments:
        return None
    if len(comments) == 1:
        return comments[0][1]
    return "\n\n".join(f"{name}: {comment}" for name, comment in comments)


def _v7_merge_lead_assessment_rows(session, now, actor=_V7_MIGRATION_ACTOR,
                                   comment=_V7_MIGRATION_COMMENT) -> None:
    """Create a single v7 Lead Assessment row and move its EAV values.

    The insert is guarded by the survivor name.  A successfully migrated
    project has that row forever, so replay is a true no-op.  The old rows are
    not deleted: history stays attached to their original task ids while their
    EAV inputs move to the sole active owner and the old rows are retired.

    ``actor``/``comment`` default to the frozen v7 history values (v7 replay is
    byte-identical); the v8 repair fold runs the same merge under its own name.
    """
    rows = db.fetch_all(session, """
        SELECT pt.task_id, pt.project_id, pt.task_name, pt.status, pt.assigned_to, pt.comments,
               pt.priority, pt.actual_start, pt.actual_finish,
               pt.business_plan_enabled, pt.business_plan_year
        FROM project_tasks pt
        WHERE pt.task_name IN :source_names
          AND pt.project_id NOT IN (
              SELECT project_id FROM project_tasks WHERE task_name = :merged_name
          )
        ORDER BY pt.project_id,
                 CASE pt.task_name
                   WHEN 'Area Definition' THEN 1
                   WHEN 'Thickness Estimation' THEN 2
                   WHEN 'GRV Inputs' THEN 3
                   WHEN 'Resource Assessment' THEN 4
                 END,
                 pt.task_id
    """, {"source_names": list(_V7_LEGACY_LEAD_STEPS),
          "merged_name": _V7_LEAD_ASSESSMENT})
    by_project = {}
    for row in rows:
        by_project.setdefault(row["project_id"], []).append(row)

    for project_id, source_rows in by_project.items():
        names = [row["task_name"] for row in source_rows]
        if names != list(_V7_LEGACY_LEAD_STEPS):
            raise RuntimeError(
                f"Migration v7 refused to merge project {project_id}: expected exactly "
                f"{list(_V7_LEGACY_LEAD_STEPS)!r}, found {names!r}.")
        _v7_assert_disjoint_field_keys(session, project_id, source_rows)
        statuses = [row["status"] or "Not Assigned" for row in source_rows]
        invalid_statuses = sorted({value for value in statuses if value not in _V7_STATUS_RANK})
        if invalid_statuses:
            raise RuntimeError(
                f"Migration v7 refused to merge project {project_id}: unsupported "
                f"legacy task status(es) {invalid_statuses!r}.")
        status = min(statuses, key=lambda value: _V7_STATUS_RANK.get(value, -1))
        assignee = next((row["assigned_to"] for row in source_rows if row["assigned_to"]), None)
        starts = [row["actual_start"] for row in source_rows if row["actual_start"]]
        finishes = [row["actual_finish"] for row in source_rows if row["actual_finish"]]
        priority = min((row["priority"] or "Medium" for row in source_rows),
                       key=lambda value: _V7_PRIORITY_RANK.get(value, 1))
        anchor = source_rows[0]
        result = db.execute(session, """
            INSERT INTO project_tasks (
                project_id, sequence_no, task_name, stage_group, assigned_to, status,
                actual_start, actual_finish, comments, priority, business_plan_enabled,
                business_plan_year, is_active, last_updated
            ) VALUES (:project_id, 1, :task_name, 'Lead Assessment', :assigned_to, :status,
                      :actual_start, :actual_finish, :comments, :priority, :business_plan_enabled,
                      :business_plan_year, 1, :now)
        """, {"project_id": project_id, "task_name": _V7_LEAD_ASSESSMENT,
              "assigned_to": assignee, "status": status,
              "actual_start": min(starts) if starts else None,
              "actual_finish": max(finishes) if status == "Approved" and finishes else None,
              "comments": _v7_merged_comments(source_rows),
              "priority": priority,
              "business_plan_enabled": anchor["business_plan_enabled"] or 0,
              "business_plan_year": anchor["business_plan_year"], "now": now})
        merged_task_id = result.lastrowid
        db.execute(session, """
            UPDATE task_dynamic_fields
            SET task_id = :merged_task_id, updated_at = :now
            WHERE task_id IN :source_task_ids
        """, {"merged_task_id": merged_task_id, "now": now,
              "source_task_ids": [row["task_id"] for row in source_rows]})
        _v7_log(session, merged_task_id, project_id, status, actor=actor, comment=comment)
        db.execute(session, """
            UPDATE project_tasks
            SET is_active = 0
            WHERE task_id IN :source_task_ids AND is_active != 0
        """, {"source_task_ids": [row["task_id"] for row in source_rows]})


def _v7_resequence(session) -> None:
    """Freeze v7's nine active prospect task slots on every materialized record."""
    for task_name, sequence_no in _V7_RESEQUENCE.items():
        db.execute(session, """
            UPDATE project_tasks
            SET sequence_no = :sequence_no
            WHERE task_name = :task_name AND is_active = 1
              AND sequence_no != :sequence_no
        """, {"task_name": task_name, "sequence_no": sequence_no})


def _migrate_v7_lead_assessment_single_step(session, engine) -> None:
    """v7: fold four Lead Assessment rows into one guarded lifecycle row."""
    _v7_merge_lead_assessment_rows(session, utc_now_str())
    _v7_resequence(session)


# v8: history vocabulary for the repair replay of the v7 fold.  Frozen here for
# the same reason as the v7 constants above.
_V8_MIGRATION_ACTOR = "System (migration v8)"
_V8_MIGRATION_COMMENT = "Merged from 4 Lead Assessment steps (migration v8 repair)"


def _migrate_v8_repair_lead_assessment_fold(session, engine) -> None:
    """v8: repair projects the v7 fold missed because a stale server made them.

    A pre-v7 server process that kept running after the database was already
    stamped v7 created NEW prospect projects carrying the old four-row Lead
    Assessment template (observed on dev projects 29/30).  Those projects are
    broken under v7+ code, and v7 itself never runs again on a v7-stamped
    database.  This step re-runs the SAME frozen merge (guarded per project by
    the absence of a "Lead Assessment" row, so it is a no-op on every healthy
    project) under the v8 actor/comment, then re-freezes the sequence slots.
    """
    _v7_merge_lead_assessment_rows(session, utc_now_str(),
                                   actor=_V8_MIGRATION_ACTOR,
                                   comment=_V8_MIGRATION_COMMENT)
    _v7_resequence(session)


# v9: priority becomes a LEAD/WELL-LEVEL attribute (projects.priority).  The
# backfill vocabulary is frozen here rather than imported from
# workflow.constants: a shipped migration must not change behavior when the
# runtime vocabulary later changes.
_V9_PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}


def _migrate_v9_project_priority(session, engine) -> None:
    """v9: add nullable ``projects.priority`` TEXT and backfill it once.

    Column add is guarded on existence (the v6 ground_elevation pattern).  The
    backfill writes the most urgent value among the project's OPEN work -- its
    active, not-yet-Approved task rows carrying a recognized High/Medium/Low --
    defaulting to 'Low' when none apply, which reproduces what the board's old
    derived ``_lead_priority`` reported.  Only rows still NULL are written, so
    a replay (or a hand-populated column) changes nothing.
    """
    columns = {column["name"] for column in inspect(engine).get_columns("projects")}
    if "priority" not in columns:
        db.execute(session, "ALTER TABLE projects ADD COLUMN priority TEXT")
    rows = db.fetch_all(session, """
        SELECT p.project_id, pt.priority
        FROM projects p
        LEFT JOIN project_tasks pt
          ON pt.project_id = p.project_id
         AND pt.is_active = 1
         AND pt.status != 'Approved'
         AND pt.priority IN ('High', 'Medium', 'Low')
        WHERE p.priority IS NULL
    """)
    best_by_project = {}
    for row in rows:
        rank = _V9_PRIORITY_RANK.get(row["priority"], _V9_PRIORITY_RANK["Low"])
        current = best_by_project.get(row["project_id"], _V9_PRIORITY_RANK["Low"])
        best_by_project[row["project_id"]] = min(rank, current)
    for project_id, rank in best_by_project.items():
        value = next(name for name, r in _V9_PRIORITY_RANK.items() if r == rank)
        db.execute(session, """
            UPDATE projects SET priority = :priority
            WHERE project_id = :project_id AND priority IS NULL
        """, {"priority": value, "project_id": project_id})


def _migrate_v10_business_plan_execution(session, engine) -> None:
    """v10: carry legacy BPE values into the approved contracts.

    This is deliberately data-only. Stable task and formation identifiers stay
    in place; combined detail pages are a read/write projection. Values with no
    approved counterpart (PDA ``Booked``, the file-type confirmations) are
    preserved untouched rather than guessed.

    The four retired fluid labels DO have approved counterparts and are mapped:
    Dry -> Dry Hole, Water -> Water Bearing, Condensate -> Oil over Gas,
    Liquid -> Oil. Idempotent by construction -- the WHERE list names only the
    retired spellings, so a replay matches nothing.
    """
    now = utc_now_str()
    # Formation-envelope fluid is retained for compatibility even though
    # automation now reads every Pay Interval; both tables and the legacy EAV
    # fluid keys carry the SAME four-way mapping so no reader sees a mix.
    fluid_case = """CASE lower(trim({column}))
              WHEN 'dry' THEN 'Dry Hole'
              WHEN 'water' THEN 'Water Bearing'
              WHEN 'condensate' THEN 'Oil over Gas'
              WHEN 'liquid' THEN 'Oil'
              ELSE {column} END"""
    for table in ("project_formations", "project_formation_pay_intervals"):
        db.execute(session, f"""
            UPDATE {table} SET fluid = {fluid_case.format(column="fluid")}
            WHERE lower(trim(fluid)) IN ('dry', 'water', 'condensate', 'liquid')
        """)

    # The step-level EAV fluid selects (reporting.py's fluid ladder reads all
    # four) store the same vocabulary as free text, so they move with it.
    db.execute(session, f"""
        UPDATE task_dynamic_fields
        SET field_value = {fluid_case.format(column="field_value")}, updated_at = :now
        WHERE field_key IN ('final_fluid_type', 'resource_update_fluid_type',
                            'post_drill_fluid_type', 'quicklook_fluid_type')
          AND lower(trim(field_value)) IN ('dry', 'water', 'condensate', 'liquid')
    """, {"now": now})

    def field_value(task_id, key):
        row = db.fetch_one(session, """
            SELECT field_value FROM task_dynamic_fields
            WHERE task_id = :task_id AND field_key = :field_key
        """, {"task_id": task_id, "field_key": key})
        return row.get("field_value") if row else None

    def insert_if_absent(task_id, key, value):
        if value is None:
            return
        db.execute(session, """
            INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
            VALUES (:task_id, :field_key, :field_value, :now)
            ON CONFLICT(task_id, field_key) DO NOTHING
        """, {"task_id": task_id, "field_key": key, "field_value": str(value), "now": now})

    # The retired AAP sentence explicitly represented both repositories, so a
    # stored answer can safely seed both independent confirmations.
    for task in db.fetch_all(session, """
        SELECT task_id FROM project_tasks WHERE task_name = 'Aramco Picks'
    """):
        old = field_value(task["task_id"], "aramco_picks_loaded")
        if old is not None:
            insert_if_absent(task["task_id"], "aap_petrel_loaded", old)
            insert_if_absent(task["task_id"], "aap_geoknowledge_loaded", old)

    # The new Flowback confirmation is true only when both old confirmations
    # were true. A partial legacy record remains incomplete.
    truthy = {"1", "true", "yes", "on"}
    for task in db.fetch_all(session, """
        SELECT task_id FROM project_tasks WHERE task_name = 'Flowback Results'
    """):
        sheet = field_value(task["task_id"], "flowback_sheet")
        slide = field_value(task["task_id"], "flowback_slide")
        if sheet is not None or slide is not None:
            merged = "1" if (str(sheet or "").strip().lower() in truthy
                             and str(slide or "").strip().lower() in truthy) else "0"
            insert_if_absent(task["task_id"], "flowback_shared_confirmed", merged)

        raw_rows = field_value(task["task_id"], "flowback_stages_rows")
        if raw_rows is None:
            continue
        try:
            rows = json.loads(raw_rows)
        except (TypeError, ValueError):
            continue
        if not isinstance(rows, list):
            continue
        global_area = field_value(task["task_id"], "flowback_dynamic_area_km2")
        global_ogip = field_value(task["task_id"], "flowback_dynamic_ogip_bcf")
        changed = False
        migrated = []
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                migrated.append(row)
                continue
            mapped = {
                "id": str(row.get("id") or row.get("_id") or f"legacy-{task['task_id']}-{index}"),
                "formation": row.get("formation", row.get("flowback_formation", "")),
                "top_md": row.get("top_md", row.get("flowback_top_md", "")),
                "base_md": row.get("base_md", row.get("flowback_base_md", "")),
                "dynamic_area_km2": row.get("dynamic_area_km2", global_area if len(rows) == 1 else ""),
                "dynamic_ogip_bcf": row.get("dynamic_ogip_bcf", global_ogip if len(rows) == 1 else ""),
                "gas_rate_mmscfd": row.get("gas_rate_mmscfd", row.get("flowback_gas_rate_mmscfd", "")),
                "water_rate_bwpd": row.get("water_rate_bwpd", row.get("flowback_water_rate_bwpd", "")),
                "liquid_rate_bpd": row.get("liquid_rate_bpd", row.get("flowback_liquid_rate_bpd", "")),
                "choke_size_in": row.get("choke_size_in", row.get("flowback_choke_size_in", "")),
                "fwhp_psi": row.get("fwhp_psi", row.get("flowback_fwhp_psi", "")),
            }
            migrated.append(mapped)
            changed = changed or mapped != row
        if changed:
            db.execute(session, """
                UPDATE task_dynamic_fields SET field_value = :value, updated_at = :now
                WHERE task_id = :task_id AND field_key = 'flowback_stages_rows'
            """, {"value": json.dumps(migrated, separators=(",", ":")),
                  "now": now, "task_id": task["task_id"]})


_V11_ACTOR = "migration:v11"
_V11_EVENT = "TVDSS Sign Normalized"

# Where a signed TVDSS could have been stored: two formation tables, plus the
# lead's own Section 3 reading in the EAV. Every other depth field (MD, TVD,
# TWT, ground elevation) is untouched -- this is about ONE field's sign
# representation, not about depths in general.
# Both tables carry their own project_id and an `id` primary key, so each is
# reachable without a join.
_V11_COLUMN_SOURCES = (
    ("project_formations", ("top_tvdss_ft", "base_tvdss_ft")),
    ("project_formation_pay_intervals", ("top_tvdss_ft", "base_tvdss_ft")),
)
_V11_FIELD_KEY = "top_formation_tvdss_ft"


def _v11_log(session, task_id, project_id, task_name, old_value, new_value, detail):
    """One immutable event per converted value, carrying the PRIOR signed one.

    ``old_status`` holds the value as it was stored. That is the whole point of
    the migration event: the sign is no longer recoverable from the row itself,
    so the audit trail is where a reader goes to find what was originally
    entered.
    """
    db.execute(session, """
        INSERT INTO task_history (task_id, project_id, task_name, action_type,
                                  old_status, new_status, changed_at, changed_by, comment)
        VALUES (:task_id, :project_id, :task_name, :action_type,
                :old_status, :new_status, :changed_at, :changed_by, :comment)
    """, {"task_id": task_id, "project_id": project_id, "task_name": task_name,
          "action_type": _V11_EVENT, "old_status": str(old_value),
          "new_status": str(new_value), "changed_at": utc_now_str(),
          "changed_by": _V11_ACTOR,
          "comment": f"TVDSS stored as a magnitude; prior signed value {old_value} ({detail})."})


def _migrate_v11_tvdss_positive(session, engine) -> None:
    """Card 3H. Store every TVDSS as a magnitude, and keep the old sign visible.

    TVDSS was the one field exempted from the application's no-negative rule,
    because a horizon above the datum is legitimately negative. The card
    reverses that: storage becomes the magnitude and the sign survives only as
    an Audit Trail event. Units and datum meaning do not change -- a depth of
    -120 and one of 120 refer to the same horizon; only the representation
    moves.

    Idempotent by construction: it selects rows that are still negative, so a
    replay finds none and writes no second event.
    """
    # task_history hangs off a task. Formation rows belong to the WELL rather
    # than to any one step, so an event about them is anchored the way
    # workflow.update_project_name anchors a rename: on the record's first
    # task, preferring the step that last wrote the row when it recorded one.
    anchors = {}

    def anchor_task(project_id, source_task_id):
        if source_task_id:
            return source_task_id
        if project_id not in anchors:
            row = db.fetch_one(session, """
                SELECT task_id FROM project_tasks WHERE project_id = :project_id
                ORDER BY sequence_no, task_id LIMIT 1
            """, {"project_id": project_id})
            anchors[project_id] = row["task_id"] if row else None
        return anchors[project_id]

    for table, columns in _V11_COLUMN_SOURCES:
        for column in columns:
            rows = db.fetch_all(session, f"""
                SELECT id AS row_id, project_id, formation, source_task_id,
                       {column} AS value
                FROM {table}
                WHERE {column} IS NOT NULL AND {column} < 0
            """)
            for row in rows:
                old_value = row["value"]
                new_value = abs(float(old_value))
                db.execute(session, f"""
                    UPDATE {table} SET {column} = :value WHERE id = :row_id
                """, {"value": new_value, "row_id": row["row_id"]})
                task_id = anchor_task(row["project_id"], row["source_task_id"])
                if task_id is None:
                    continue  # orphaned rows carry no auditable owner
                _v11_log(session, task_id, row["project_id"],
                         f"Formations ({row['formation']})",
                         old_value, new_value, f"{table}.{column}")

    # The lead's Section 3 reading lives in the EAV, where every value is text,
    # so the filter is a leading minus rather than a numeric comparison. A row
    # that does not parse as a number is left exactly as it is -- this
    # migration converts signs, it does not clean up bad data.
    rows = db.fetch_all(session, """
        SELECT d.task_id AS task_id, d.field_value AS value,
               t.project_id AS project_id, t.task_name AS task_name
        FROM task_dynamic_fields d
        JOIN project_tasks t ON t.task_id = d.task_id
        WHERE d.field_key = :field_key AND TRIM(d.field_value) LIKE '-%'
    """, {"field_key": _V11_FIELD_KEY})
    for row in rows:
        raw = str(row["value"]).strip()
        try:
            new_value = abs(float(raw))
        except (TypeError, ValueError):
            continue
        text = str(int(new_value)) if float(new_value).is_integer() else str(new_value)
        db.execute(session, """
            UPDATE task_dynamic_fields SET field_value = :value, updated_at = :now
            WHERE task_id = :task_id AND field_key = :field_key
        """, {"value": text, "now": utc_now_str(),
              "task_id": row["task_id"], "field_key": _V11_FIELD_KEY})
        _v11_log(session, row["task_id"], row["project_id"], row["task_name"],
                 raw, text, _V11_FIELD_KEY)


def _migrate_v12_project_nucd_area(session, engine) -> None:
    """v12: add the nullable ``projects.nucd_area`` TEXT column.

    Record-level free text (the operating area a lead/well sits in), fed by
    import_excel's "NUCD Area" column and read by the Portfolio Analysis table.
    There is nothing to backfill and nothing to derive it from: the column
    arrives NULL everywhere, and a blank stays blank until an import states an
    area. Inventing one from the field or seismic block would be a guess
    presented as data.

    Guarded on column existence (the v6 ground_elevation pattern), so a
    database already carrying the column replays unchanged.
    """
    columns = {column["name"] for column in inspect(engine).get_columns("projects")}
    if "nucd_area" not in columns:
        db.execute(session, "ALTER TABLE projects ADD COLUMN nucd_area TEXT")


# List of (version, fn) dispatched by run() in ascending order against the
# stored schema_version. Append new steps with the next integer version and
# bump LATEST_SCHEMA_VERSION to match; never edit or remove a shipped step.
MIGRATIONS = [
    (2, _migrate_v2_users_password_hash),
    (3, _migrate_v3_rename_quicklook_logs),
    (4, _migrate_v4_bp_step_merges),
    (5, _migrate_v5_prospect_template_restructure),
    (6, _migrate_v6_ground_elevation),
    (7, _migrate_v7_lead_assessment_single_step),
    (8, _migrate_v8_repair_lead_assessment_fold),
    (9, _migrate_v9_project_priority),
    (10, _migrate_v10_business_plan_execution),
    (11, _migrate_v11_tvdss_positive),
    (12, _migrate_v12_project_nucd_area),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(session, engine) -> None:
    """Create or upgrade the schema, seed base data, stamp the current version.

    Fresh database (no ``app_settings`` table yet): ``create_all`` builds the
    full current shape and no migration steps run. Existing database: refuse a
    ``schema_version`` newer than this code knows (older code must never write
    into a newer-shaped database), let ``create_all`` add any newly-modeled
    tables, then apply every MIGRATIONS step newer than the stored version in
    ascending order. Either way the database ends stamped
    LATEST_SCHEMA_VERSION with base data ensured.
    """
    inspector = inspect(engine)
    fresh = "app_settings" not in inspector.get_table_names()
    stored = 0
    if not fresh:
        stored = _get_schema_version(session)
        if stored > LATEST_SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema_version ({stored}) is newer than this code's "
                f"LATEST_SCHEMA_VERSION ({LATEST_SCHEMA_VERSION}). This application code is "
                "older than the database it is pointed at -- update the code (or point "
                "SEGMENT_TRACKER_DB_PATH at the right file) instead of downgrading the database."
            )

    Base.metadata.create_all(engine)

    # Upfront write lock for the migrate/ensure/seed transaction (SQLite BEGIN
    # IMMEDIATE; a Postgres advisory lock would slot into begin_write).
    db.begin_write(session)
    if not fresh:
        for version, step in sorted(MIGRATIONS):
            if version > stored:
                step(session, engine)
    _ensure_base_data(session)
    _set_schema_version(session, LATEST_SCHEMA_VERSION)
    session.commit()
