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

LATEST_SCHEMA_VERSION = 2


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


# List of (version, fn) dispatched by run() in ascending order against the
# stored schema_version. Append new steps with the next integer version and
# bump LATEST_SCHEMA_VERSION to match; never edit or remove a shipped step.
MIGRATIONS = [
    (2, _migrate_v2_users_password_hash),
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
