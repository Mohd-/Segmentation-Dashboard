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

from sqlalchemy import inspect

import config
import db
from helpers import utc_now_str
from models import Base

LATEST_SCHEMA_VERSION = 4


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
    passwords, written by add_users.py, checked by POST /api/login).

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


# List of (version, fn) dispatched by run() in ascending order against the
# stored schema_version. Append new steps with the next integer version and
# bump LATEST_SCHEMA_VERSION to match; never edit or remove a shipped step.
MIGRATIONS = [
    (2, _migrate_v2_users_password_hash),
    (3, _migrate_v3_rename_quicklook_logs),
    (4, _migrate_v4_bp_step_merges),
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
