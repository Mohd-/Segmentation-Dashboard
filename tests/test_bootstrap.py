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


def test_new_project_gets_27_active_tasks(client):
    pid = create_project(client, "BOOTSTRAP-PROJECT-1")
    tasks = get_tasks(client, pid)
    assert len(tasks) == 27
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


def _quicklook_task_names(db_path):
    """(sorted quicklook task names, stamped schema_version, project names) read
    raw -- the assertion tuple for the v3 rename's upgrade-and-replay test."""
    conn = raw_sqlite_connect(db_path)
    try:
        names = sorted(row["task_name"] for row in conn.execute(
            "SELECT task_name FROM project_tasks WHERE task_name LIKE 'Quicklook%'"))
        version = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()["value"]
        projects = {row["project_name"] for row in
                    conn.execute("SELECT project_name FROM projects")}
    finally:
        conn.close()
    return names, version, projects


def test_migration_v3_renames_the_quicklook_step_in_place(client, app_modules):
    """Upgrade-and-replay for step 3 (the "Quicklook Logs Interpretation" ->
    "Quicklook Logs" rename): reshape a fresh DB to the v2 form with raw sqlite3
    (the step row back under its old name, stamped schema_version 2),
    re-bootstrap, and assert the row was renamed in place, the version stamped
    current and the project preserved. Then bootstrap once more and assert
    nothing changes (the step only touches rows still holding the old name, so
    the replay is a no-op)."""
    _, dbmod = app_modules
    import migrations

    pid = create_project(client, "MIGRATE-QUICKLOOK-1")
    task_ids_before = {t["task_name"]: t["task_id"] for t in get_tasks(client, pid)}

    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.executescript("""
            UPDATE project_tasks SET task_name = 'Quicklook Logs Interpretation'
                WHERE task_name = 'Quicklook Logs';
            UPDATE app_settings SET value = '2' WHERE key = 'schema_version';
        """)
        conn.commit()
    finally:
        conn.close()

    _rebootstrap(dbmod, client.db_path)
    names, version, projects = _quicklook_task_names(client.db_path)
    assert names == ["Quicklook Logs"]
    assert version == str(migrations.LATEST_SCHEMA_VERSION)
    assert "MIGRATE-QUICKLOOK-1" in projects
    # Renamed IN PLACE: the row (and therefore its dynamic fields, history and
    # formation source_task_id links) is the same one, not a recreated stub.
    task_ids_after = {t["task_name"]: t["task_id"] for t in get_tasks(client, pid)}
    assert task_ids_after["Quicklook Logs"] == task_ids_before["Quicklook Logs"]

    _rebootstrap(dbmod, client.db_path)
    assert _quicklook_task_names(client.db_path) == (names, version, projects)


def test_migration_v3_skips_a_project_holding_both_quicklook_names(client, app_modules):
    """A project carrying BOTH names (a hand-inserted legacy row) cannot be
    renamed: project_tasks has UNIQUE(project_id, task_name), so the UPDATE
    would abort the whole bootstrap. The step's guard skips those rows -- the
    database still upgrades and stamps current, leaving the stale old-name row
    for manual reconciliation."""
    _, dbmod = app_modules
    import migrations

    pid = create_project(client, "MIGRATE-QUICKLOOK-BOTH-1")

    conn = raw_sqlite_connect(client.db_path)
    try:
        row = conn.execute(
            "SELECT sequence_no, stage_group FROM project_tasks "
            "WHERE project_id = ? AND task_name = 'Quicklook Logs'", (pid,)).fetchone()
        conn.execute(
            "INSERT INTO project_tasks (project_id, sequence_no, task_name, stage_group, "
            "status, priority, is_active, last_updated) "
            "VALUES (?, ?, 'Quicklook Logs Interpretation', ?, 'Not Assigned', 'Medium', 1, "
            "datetime('now'))",
            (pid, row["sequence_no"], row["stage_group"]))
        conn.execute("UPDATE app_settings SET value = '2' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()

    _rebootstrap(dbmod, client.db_path)
    names, version, _projects = _quicklook_task_names(client.db_path)
    assert names == ["Quicklook Logs", "Quicklook Logs Interpretation"]
    assert version == str(migrations.LATEST_SCHEMA_VERSION)


# ---------------------------------------------------------------------------
# v4: the BP step merges (31 -> 27 steps)
# ---------------------------------------------------------------------------

# The pre-v4 shape of the four merged-away steps: (task_name, sequence_no,
# stage_group). Reinstated by raw sqlite3 to build a v3-shaped database.
_V3_RETIRED_ROWS = [
    ("Post-Drilling Resource Assessment", 20, "Post-Drilling"),
    ("URED Update", 23, "Post-Drilling"),
    ("Executive Summary Final", 27, "Post-Testing"),
    ("Resource Assessment Update", 30, "Post-Testing"),
]
# The pre-v4 sequence numbers of the survivors that moved.
_V3_SEQUENCES = {
    "SAD Model": 21, "Executive Summary": 22,
    "Post-Well Outcome & Decision Gate": 24, "Flowback Results": 25,
    "SAD Update": 26, "Final Log Analysis": 28,
    "PVAD Structural MTR": 29, "PDA": 31,
}


def _make_v3_shaped(db_path, project_id, legacy_fields=()):
    """Reshape a fresh (v4) database back to the v3 31-step form, raw.

    Reinstates the four retired steps as ACTIVE rows at their old sequence
    numbers, restores the survivors' old numbers, optionally hangs
    ``legacy_fields`` -- an iterable of (task_name, field_key, field_value) --
    off those reinstated rows, and stamps schema_version 3.
    """
    conn = raw_sqlite_connect(db_path)
    try:
        for task_name, sequence_no, stage_group in _V3_RETIRED_ROWS:
            conn.execute(
                "INSERT INTO project_tasks (project_id, sequence_no, task_name, stage_group, "
                "status, priority, is_active, last_updated) "
                "VALUES (?, ?, ?, ?, 'Not Assigned', 'Medium', 1, datetime('now'))",
                (project_id, sequence_no, task_name, stage_group))
        for task_name, sequence_no in _V3_SEQUENCES.items():
            conn.execute("UPDATE project_tasks SET sequence_no = ? WHERE task_name = ?",
                         (sequence_no, task_name))
        for task_name, field_key, field_value in legacy_fields:
            row = conn.execute(
                "SELECT task_id FROM project_tasks WHERE project_id = ? AND task_name = ?",
                (project_id, task_name)).fetchone()
            conn.execute(
                "INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at) "
                "VALUES (?, ?, ?, datetime('now'))",
                (row["task_id"], field_key, field_value))
        conn.execute("UPDATE app_settings SET value = '3' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()


def _bp_step_shape(db_path, project_id):
    """(active [(sequence_no, task_name)], inactive names, retained EAV rows,
    stamped version) -- the assertion tuple for the v4 upgrade-and-replay."""
    conn = raw_sqlite_connect(db_path)
    try:
        active = [(row["sequence_no"], row["task_name"]) for row in conn.execute(
            "SELECT sequence_no, task_name FROM project_tasks "
            "WHERE project_id = ? AND is_active = 1 ORDER BY sequence_no", (project_id,))]
        inactive = sorted(row["task_name"] for row in conn.execute(
            "SELECT task_name FROM project_tasks WHERE project_id = ? AND is_active = 0",
            (project_id,)))
        fields = sorted((row["task_name"], row["field_key"], row["field_value"])
                        for row in conn.execute(
                            "SELECT pt.task_name, tdf.field_key, tdf.field_value "
                            "FROM project_tasks pt JOIN task_dynamic_fields tdf "
                            "ON tdf.task_id = pt.task_id WHERE pt.project_id = ?", (project_id,)))
        version = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()["value"]
    finally:
        conn.close()
    return active, inactive, fields, version


def test_migration_v4_retires_the_merged_bp_steps_and_keeps_their_data(client, app_modules):
    """Upgrade-and-replay for step 4 (the BP step merges). A v3-shaped database
    holding data on the four soon-to-be-retired steps must come out with those
    rows INACTIVE, their task_dynamic_fields untouched, the survivors
    renumbered contiguously to 1-27, and a replay that changes nothing."""
    _, dbmod = app_modules
    import migrations

    pid = create_project(client, "MIGRATE-MERGE-1")
    task_ids_before = {t["task_name"]: t["task_id"] for t in get_tasks(client, pid)}
    _make_v3_shaped(client.db_path, pid, legacy_fields=[
        ("Post-Drilling Resource Assessment", "post_drill_piip_gas_mean", "9.25"),
        ("Resource Assessment Update", "resource_update_fluid_type", "Gas Condensate"),
        ("URED Update", "some_ured_note", "kept"),
        ("Executive Summary Final", "some_final_note", "kept"),
    ])

    _rebootstrap(dbmod, client.db_path)
    active, inactive, fields, version = _bp_step_shape(client.db_path, pid)

    assert version == str(migrations.LATEST_SCHEMA_VERSION)
    # Retired, not deleted.
    assert inactive == ["Executive Summary Final", "Post-Drilling Resource Assessment",
                        "Resource Assessment Update", "URED Update"]
    # ... and every one of their inputs survived, verbatim.
    assert ("Post-Drilling Resource Assessment", "post_drill_piip_gas_mean", "9.25") in fields
    assert ("Resource Assessment Update", "resource_update_fluid_type", "Gas Condensate") in fields
    assert ("URED Update", "some_ured_note", "kept") in fields
    assert ("Executive Summary Final", "some_final_note", "kept") in fields
    # The survivors are the 27 template steps, renumbered contiguously.
    import workflow
    assert active == [(seq, name) for seq, name, _stage in workflow.PIPELINE_TEMPLATES]
    assert [seq for seq, _name in active] == list(range(1, 28))
    # Renumbered IN PLACE -- same rows, so their fields/history/folders follow.
    task_ids_after = {t["task_name"]: t["task_id"] for t in get_tasks(client, pid)}
    for name, task_id in task_ids_before.items():
        assert task_ids_after[name] == task_id

    # Replay: a second bootstrap against the upgraded file changes nothing.
    _rebootstrap(dbmod, client.db_path)
    assert _bp_step_shape(client.db_path, pid) == (active, inactive, fields, version)


def test_migration_v4_is_a_no_op_on_an_already_merged_database(client, app_modules):
    """A v3-stamped database that never carried the retired steps (or was
    hand-merged already) upgrades cleanly: both guards -- ``is_active != 0``
    and ``sequence_no != :sequence_no`` -- make the step a no-op."""
    _, dbmod = app_modules
    import migrations

    pid = create_project(client, "MIGRATE-MERGE-2")
    before = _bp_step_shape(client.db_path, pid)

    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute("UPDATE app_settings SET value = '3' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()

    _rebootstrap(dbmod, client.db_path)
    assert _bp_step_shape(client.db_path, pid) == before
    assert before[3] == str(migrations.LATEST_SCHEMA_VERSION)


def test_migration_v4_leaves_retired_step_data_readable_through_the_fallbacks(client, app_modules):
    """The point of retiring rather than deleting: a well whose numbers were
    entered on a merged-away step still reads them back after the upgrade --
    through /detail's fields map (retired-inclusive), the composed overview's
    surviving-then-legacy source list, and the Portfolio's field-keyed read."""
    _, dbmod = app_modules

    pid = create_project(client, "MIGRATE-MERGE-3", pipeline_type="bp",
                         business_plan_enabled=True, business_plan_year=2029)
    _make_v3_shaped(client.db_path, pid, legacy_fields=[
        ("Resource Assessment Update", "resource_update_gas_mean", "12.5"),
        ("Post-Drilling Resource Assessment", "post_drill_fluid_type", "Gas Condensate"),
    ])
    _rebootstrap(dbmod, client.db_path)

    detail = client.get(f"/api/projects/{pid}/detail").get_json()
    # The retired step is NOT a component any more...
    assert "Resource Assessment Update" not in {t["task_name"] for t in detail["tasks"]}
    assert len(detail["tasks"]) == 27
    # ... but its bucket is still in the fields map the client reads.
    assert detail["fields"]["Resource Assessment Update"]["resource_update_gas_mean"] == "12.5"
    assert detail["overview"]["post_drill_estimation"] == "12.5"

    row = next(r for r in client.get("/api/portfolio/rows").get_json()["rows"]
               if r["project_id"] == pid)
    assert row["mean_ogip"] == "12.5"
    assert row["fluid"] == "Gas Condensate"

    # Both filled -> the SURVIVING step wins everywhere. Entering the same keys
    # on the step that absorbed the retired one supersedes the legacy value,
    # rather than the two competing by luck of row order.
    sad_update = next(t for t in detail["tasks"] if t["task_name"] == "SAD Update")
    sad_model = next(t for t in detail["tasks"] if t["task_name"] == "SAD Model")
    assert client.patch(f"/api/tasks/{sad_update['task_id']}/dynamic-fields",
                        json={"fields": {"resource_update_gas_mean": "20.0"}}).status_code == 200
    assert client.patch(f"/api/tasks/{sad_model['task_id']}/dynamic-fields",
                        json={"fields": {"post_drill_fluid_type": "Dry"}}).status_code == 200

    detail = client.get(f"/api/projects/{pid}/detail").get_json()
    assert detail["overview"]["post_drill_estimation"] == "20.0"
    # The legacy bucket is still there, just outranked -- nothing was destroyed.
    assert detail["fields"]["Resource Assessment Update"]["resource_update_gas_mean"] == "12.5"
    row = next(r for r in client.get("/api/portfolio/rows").get_json()["rows"]
               if r["project_id"] == pid)
    assert row["mean_ogip"] == "20.0"
    assert row["fluid"] == "Dry"


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
