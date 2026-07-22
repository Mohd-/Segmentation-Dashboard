"""Tests for schema bootstrap and in-place migrations (migrations.run).

A fresh database is created straight from models.py and stamped
LATEST_SCHEMA_VERSION (no migration steps run); an existing database is
upgraded in place by the MIGRATIONS steps newer than its stored
schema_version; a database stamped NEWER than the code's
LATEST_SCHEMA_VERSION is refused rather than silently adopted. Every shipped
migration step gets an upgrade-and-replay test here (CONTRIBUTING.md recipe
5): reshape a fresh DB to the OLD form with raw sqlite3, re-bootstrap, assert
the upgrade, then bootstrap once more and assert nothing changes.
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


def test_fresh_bootstrap_stamps_latest_schema_version(client):
    import migrations

    conn = raw_sqlite_connect(client.db_path)
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["value"] == str(migrations.LATEST_SCHEMA_VERSION)


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


def _rebootstrap(dbmod, db_path):
    """Re-run the full bootstrap (migrations.run) against an existing file,
    exactly as an app restart would."""
    dbmod.reset_for_tests()
    dbmod.init_db(str(db_path))


def _users_shape_and_version(db_path):
    """(users column set, stamped schema_version, user count, project names)
    read raw -- the assertion tuple for the upgrade-and-replay tests."""
    conn = raw_sqlite_connect(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        version = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()["value"]
        user_count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        projects = {row["project_name"] for row in
                    conn.execute("SELECT project_name FROM projects")}
    finally:
        conn.close()
    return columns, version, user_count, projects


def test_migration_v2_upgrades_a_v1_database_in_place(client, app_modules):
    """Upgrade-and-replay for step 2 (users.password_hash): reshape a fresh DB
    to the v1 form with raw sqlite3 (users table WITHOUT password_hash, stamped
    schema_version 1, carrying a real project), re-bootstrap, and assert the
    column was added, the version stamped current, and every row preserved.
    Then bootstrap once more and assert nothing changes."""
    _, dbmod = app_modules
    import migrations

    create_project(client, "MIGRATE-KEEP-1")

    conn = raw_sqlite_connect(client.db_path)
    try:
        # The v1 users shape, column-for-column (models.py before password_hash).
        conn.executescript("""
            CREATE TABLE users_v1 (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL DEFAULT 'employee'
                    CHECK (role IN ('supervisor','staff','employee')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT
            );
            INSERT INTO users_v1 (user_id, name, role, is_active, created_at)
                SELECT user_id, name, role, is_active, created_at FROM users;
            DROP TABLE users;
            ALTER TABLE users_v1 RENAME TO users;
            UPDATE app_settings SET value = '1' WHERE key = 'schema_version';
        """)
        conn.commit()
    finally:
        conn.close()

    _rebootstrap(dbmod, client.db_path)
    columns, version, user_count, projects = _users_shape_and_version(client.db_path)
    assert "password_hash" in columns
    assert version == str(migrations.LATEST_SCHEMA_VERSION)
    assert user_count >= 3  # the seeded SEED_USERS rows survived
    assert "MIGRATE-KEEP-1" in projects

    # Replay: a second bootstrap against the upgraded file changes nothing.
    _rebootstrap(dbmod, client.db_path)
    assert _users_shape_and_version(client.db_path) == (columns, version, user_count, projects)


def test_migration_v2_tolerates_a_hand_altered_database(client, app_modules):
    """A database stamped v1 whose users table ALREADY has password_hash (the
    documented manual ALTER one-liner) must upgrade cleanly: step 2's
    column-existence guard makes it a no-op instead of a duplicate-column
    error."""
    _, dbmod = app_modules
    import migrations

    conn = raw_sqlite_connect(client.db_path)
    try:
        # Fresh DBs already carry the column; re-stamping v1 simulates a v1
        # database that was hand-ALTERed before this code ran.
        conn.execute("UPDATE app_settings SET value = '1' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()

    _rebootstrap(dbmod, client.db_path)
    columns, version, _user_count, _projects = _users_shape_and_version(client.db_path)
    assert "password_hash" in columns
    assert version == str(migrations.LATEST_SCHEMA_VERSION)


def test_bootstrap_refuses_a_database_stamped_with_a_newer_schema_version(client, app_modules):
    """A schema_version greater than LATEST_SCHEMA_VERSION means this code is
    older than the database (e.g. a newer deployment wrote it);
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
