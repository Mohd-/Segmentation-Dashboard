"""Schema bootstrap, pre-deployment.

What belongs here:
- Creating a fresh schema (via ``models.Base.metadata.create_all``), seeding
  templates and base data, and stamping ``schema_version``.

What does NOT belong here:
- Runtime domain logic (workflow.py) -- although bootstrap may call domain
  helpers (e.g. ``workflow.seed_templates``) to populate base data.

Pre-deployment policy: nothing is deployed yet, so the database is throwaway.
There is no data to preserve across a schema change -- ``models.py`` IS the
schema. When you change it, delete the local ``.db`` file (and its ``-shm``/
``-wal`` sidecars) and restart the app; ``run()`` below recreates and reseeds
everything from scratch. LATEST_SCHEMA_VERSION stays at 1 for the whole
pre-deployment phase.

The numbered-migration skeleton (a ``MIGRATIONS`` list of ``(version, fn)``
steps, dispatched here in ascending order against ``_get_schema_version``)
resumes at first production deployment, once there is real data in the field
that a schema change must carry forward instead of discard. Until then, adding
a step here is very likely the wrong move -- edit models.py instead.

Concurrency guard: ``run`` acquires the database write lock UPFRONT via
``db.begin_write`` (SQLite ``BEGIN IMMEDIATE``) before the ensure/seed writes,
so concurrent processes cannot interleave bootstrap writes. On Postgres
``begin_write`` is a no-op and an advisory lock (pg_advisory_xact_lock) would
slot into that same call.
"""
from __future__ import annotations

from sqlalchemy import inspect

import config
import db
import workflow
from helpers import utc_now_str
from models import Base

LATEST_SCHEMA_VERSION = 1


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


# List of (version, fn). Empty during the pre-deployment phase -- see the
# module docstring. Add the first step here (with the next integer version)
# once this codebase has a production database to carry forward.
MIGRATIONS: list = []


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(session, engine) -> None:
    """Create the schema for the given engine, seed base data, stamp version 1.

    A database stamped with a schema_version newer than this code's
    LATEST_SCHEMA_VERSION means the code is older than the database -- most
    likely a pre-reset database left over from before the schema was frozen at
    version 1. Refuse to touch it rather than silently adopting a shape this
    code doesn't know about.
    """
    inspector = inspect(engine)
    if "app_settings" in inspector.get_table_names():
        stored = _get_schema_version(session)
        if stored > LATEST_SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema_version ({stored}) is newer than this code's "
                f"LATEST_SCHEMA_VERSION ({LATEST_SCHEMA_VERSION}). This database predates "
                "the pre-deployment schema reset -- delete the .db file (and its -shm/-wal "
                "sidecars) and restart the app to regenerate it."
            )

    Base.metadata.create_all(engine)

    # Upfront write lock for the ensure/seed transaction (SQLite BEGIN IMMEDIATE;
    # a Postgres advisory lock would slot into begin_write).
    db.begin_write(session)
    _ensure_base_data(session)
    workflow.seed_templates(session)
    _set_schema_version(session, LATEST_SCHEMA_VERSION)
    session.commit()
