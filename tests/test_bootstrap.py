"""Tests for schema bootstrap (migrations.run), pre-deployment.

Pre-deployment there is no upgrade-in-place migration path (see migrations.py):
a fresh database is created straight from models.py and stamped
schema_version 1, and a database stamped with a schema_version newer than the
code's LATEST_SCHEMA_VERSION is refused rather than silently adopted.
"""
from __future__ import annotations

import pytest

from conftest import create_project, get_tasks, raw_sqlite_connect


def test_fresh_bootstrap_creates_exactly_the_modeled_tables(client):
    import models

    conn = raw_sqlite_connect(client.db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    # sqlite_sequence is SQLite's own bookkeeping table for AUTOINCREMENT
    # columns, not something models.py declares.
    actual = {row["name"] for row in rows} - {"sqlite_sequence"}
    expected = set(models.Base.metadata.tables.keys())
    assert actual == expected


def test_fresh_bootstrap_stamps_schema_version_1(client):
    conn = raw_sqlite_connect(client.db_path)
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["value"] == "1"


def test_fresh_bootstrap_seeds_configured_users(client):
    import config

    resp = client.get("/api/users")
    assert resp.status_code == 200
    seeded = {(u["name"], u["role"]) for u in resp.get_json()}
    assert seeded == set(config.SEED_USERS)


def test_new_project_gets_31_active_tasks(client):
    pid = create_project(client, "BOOTSTRAP-PROJECT-1")
    tasks = get_tasks(client, pid)
    assert len(tasks) == 31
    assert all(t["is_active"] == 1 for t in tasks)


def test_bootstrap_refuses_a_database_stamped_with_a_newer_schema_version(client, app_modules):
    """A schema_version greater than LATEST_SCHEMA_VERSION means the database
    predates the pre-deployment reset (or came from a future code version);
    ``migrations.run`` must refuse to touch it rather than silently adopting an
    unknown shape."""
    _, dbmod = app_modules
    import migrations

    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute(
            "UPDATE app_settings SET value = ? WHERE key = 'schema_version'",
            (str(migrations.LATEST_SCHEMA_VERSION + 18),),
        )
        conn.commit()
    finally:
        conn.close()

    dbmod.reset_for_tests()
    with pytest.raises(RuntimeError, match="schema_version"):
        dbmod.init_db(str(client.db_path))
