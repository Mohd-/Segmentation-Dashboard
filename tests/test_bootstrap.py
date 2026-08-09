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

import json

import pytest

from conftest import create_project, get_task_by_name, get_tasks, raw_sqlite_connect, reach_task


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


def test_new_project_gets_24_active_tasks(client):
    pid = create_project(client, "BOOTSTRAP-PROJECT-1")
    tasks = get_tasks(client, pid)
    assert len(tasks) == 24
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


def _restore_v4_prospect_rows(conn):
    """Replace the current v7 prospect rows with the frozen v4 template.

    Historical migration tests start from a fresh current database.  v7 no
    longer carries the rows v5 needs to rename/merge, so merely restamping the
    version would not exercise those migration steps.  Rebuild the complete
    v4 prospect half first; the test then upgrades through v4, v5, v6 and v7.
    """
    conn.execute("DELETE FROM project_tasks WHERE stage_group IN "
                 "('Lead Assessment', 'Risk Analysis', 'Pre-Well Delivery')")
    for name, (sequence_no, stage_group) in _V4_PROSPECT_TEMPLATE.items():
        conn.execute("""
            INSERT INTO project_tasks (project_id, sequence_no, task_name, stage_group,
                                       status, priority, is_active, last_updated)
            SELECT project_id, ?, ?, ?, 'Not Assigned', 'Low', 1, datetime('now')
            FROM projects
        """, (sequence_no, name, stage_group))


def _make_v3_shaped(db_path, project_id, legacy_fields=()):
    """Reshape a fresh (v4) database back to the v3 31-step form, raw.

    Reinstates the four retired steps as ACTIVE rows at their old sequence
    numbers, restores the survivors' old numbers, optionally hangs
    ``legacy_fields`` -- an iterable of (task_name, field_key, field_value) --
    off those reinstated rows, and stamps schema_version 3.
    """
    conn = raw_sqlite_connect(db_path)
    try:
        _restore_v4_prospect_rows(conn)
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
    assert {"Executive Summary Final", "Post-Drilling Resource Assessment",
            "Resource Assessment Update", "URED Update"} <= set(inactive)
    # ... and every one of their inputs survived, verbatim.
    assert ("Post-Drilling Resource Assessment", "post_drill_piip_gas_mean", "9.25") in fields
    assert ("Resource Assessment Update", "resource_update_fluid_type", "Gas Condensate") in fields
    assert ("URED Update", "some_ured_note", "kept") in fields
    assert ("Executive Summary Final", "some_final_note", "kept") in fields
    # The BP survivors were renumbered by v4, then the full historical upgrade
    # continued into v5/v7's final current template.
    import workflow
    assert active == [(seq, name) for seq, name, _stage in workflow.PIPELINE_TEMPLATES]
    assert [seq for seq, _name in active] == [seq for seq, _name, _stage in workflow.PIPELINE_TEMPLATES]

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
    _make_v3_shaped(client.db_path, pid)
    # Remove the v3-only BP rows: v4 itself must now be a no-op, while v5/v7
    # still receive a genuine v4 prospect shape to rehearse their own work.
    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute("DELETE FROM project_tasks WHERE task_name IN "
                     "('Post-Drilling Resource Assessment', 'URED Update', "
                     "'Executive Summary Final', 'Resource Assessment Update')")
        conn.commit()
    finally:
        conn.close()
    _rebootstrap(dbmod, client.db_path)
    after = _bp_step_shape(client.db_path, pid)
    assert after[0] == [(seq, name) for seq, name, _stage in __import__('workflow').PIPELINE_TEMPLATES]
    assert after[1] == ["Area Definition", "GRV Inputs", "Resource Assessment", "Seal CoS",
                        "Thickness Estimation", "Trap CoS", "Well Creation"]
    _rebootstrap(dbmod, client.db_path)
    assert _bp_step_shape(client.db_path, pid) == after
    assert after[3] == str(migrations.LATEST_SCHEMA_VERSION)


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
    assert len(detail["tasks"]) == 24
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


# ---------------------------------------------------------------------------
# v5: the permanent 12-tracked-item prospect template
# ---------------------------------------------------------------------------

# The pre-v5 prospect template: task_name -> (sequence_no, stage_group). Frozen
# here (not imported) so these tests keep describing the v4 shape even after a
# later template edit -- the same discipline the migration's own tables follow.
_V4_PROSPECT_TEMPLATE = {
    "Reservoir Area Definition": (1, "Lead Identification"),
    "Thickness Estimation": (2, "Lead Identification"),
    "Lead Resource Assessment": (3, "Lead Identification"),
    "Seismic Signature Validation": (4, "Risking"),
    "Reservoir CoS": (5, "Risking"),
    "Trap CoS": (6, "Risking"),
    "Seal CoS": (7, "Risking"),
    "Prospect Evaluation Presentation": (8, "Segmentation"),
    "Well Creation": (9, "Pre-Well Delivery"),
    "Pre-Drilling Resource Assessment": (10, "Pre-Well Delivery"),
    "Staking Moving Tolerance": (11, "Pre-Well Delivery"),
    "Approval to Stake": (12, "Pre-Well Delivery"),
}
# v5 name -> v4 name, for the five in-place renames.
_V5_TO_V4_NAMES = {
    "Area Definition": "Reservoir Area Definition",
    "Resource Assessment": "Lead Resource Assessment",
    "Segmentation Slides": "Prospect Evaluation Presentation",
    "Moving Tolerance": "Staking Moving Tolerance",
    "Pre-Drilling GeoX Assessment": "Pre-Drilling Resource Assessment",
}
_V5_ADDED_ROWS = ("Trap and Seal CoS", "GRV Inputs", "Well Site Location")

_V5_PROSPECT_STEPS = [
    "Area Definition", "Thickness Estimation", "GRV Inputs", "Resource Assessment",
    "Reservoir CoS", "Trap and Seal CoS", "Seismic Signature Validation",
    "Segmentation Slides", "Moving Tolerance", "Approval to Stake",
    "Well Site Location", "Pre-Drilling GeoX Assessment",
]


def _make_v4_shaped(db_path, statuses=None, legacy_fields=(), project_id=None):
    """Rebuild the frozen v4 prospect half and stamp schema version 4.

    Fresh databases now start at v7, whose one Lead Assessment row cannot
    plausibly be "renamed back" into four old rows.  Reconstructing all twelve
    v4 rows is what makes v5's rename, merge and backfill paths real tests.
    """
    conn = raw_sqlite_connect(db_path)
    try:
        _restore_v4_prospect_rows(conn)
        for name, status in (statuses or {}).items():
            finish = "2026-01-05" if status == "Approved" else None
            start = "2026-01-02" if status != "Not Assigned" else None
            conn.execute("UPDATE project_tasks SET status = ?, actual_start = ?, actual_finish = ? "
                         "WHERE task_name = ?", (status, start, finish, name))
        for task_name, field_key, field_value in legacy_fields:
            row = conn.execute(
                "SELECT task_id FROM project_tasks WHERE task_name = ?"
                + (" AND project_id = ?" if project_id else ""),
                (task_name, project_id) if project_id else (task_name,)).fetchone()
            conn.execute("INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at) "
                         "VALUES (?, ?, ?, datetime('now'))",
                         (row["task_id"], field_key, field_value))
        conn.execute("UPDATE app_settings SET value = '4' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()


def _prospect_shape(db_path, project_id):
    """(active [(sequence_no, task_name, stage_group, status)], inactive names,
    retained EAV, stamped version) -- the v5 upgrade-and-replay assertion tuple."""
    conn = raw_sqlite_connect(db_path)
    try:
        active = [(row["sequence_no"], row["task_name"], row["stage_group"], row["status"])
                  for row in conn.execute(
                      "SELECT sequence_no, task_name, stage_group, status FROM project_tasks "
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


def _history(db_path, project_id, action_type):
    conn = raw_sqlite_connect(db_path)
    try:
        return [dict(row) for row in conn.execute(
            "SELECT task_name, action_type, new_status, comment FROM task_history "
            "WHERE project_id = ? AND action_type = ? ORDER BY history_id",
            (project_id, action_type))]
    finally:
        conn.close()


def test_migration_v5_restructures_an_in_progress_lead(client, app_modules):
    """Upgrade-and-replay for step 5 on an IN-PROGRESS lead with mixed statuses
    and a half-done CoS pair (Trap Approved, Seal Ready).

    Asserts every sub-step: the five renames landed in place (same task_ids),
    the merge produced ONE row whose status is the LESS advanced half, both
    halves plus Well Creation are retired (not deleted), the two new steps came
    in Not Assigned (this lead is not complete), the stage groups are the three
    v5 ones, the survivors are numbered 1-12, and a replay changes nothing."""
    _, dbmod = app_modules
    import migrations

    pid = create_project(client, "MIGRATE-V5-INPROGRESS-1")
    _make_v4_shaped(client.db_path, statuses={
        "Reservoir Area Definition": "Approved",
        "Thickness Estimation": "Approved",
        "Lead Resource Assessment": "In Progress",
        "Trap CoS": "Approved",
        "Seal CoS": "Ready",
        "Well Creation": "In Progress",
    })
    ids_before = {t["task_name"]: t["task_id"] for t in get_tasks(client, pid)}

    _rebootstrap(dbmod, client.db_path)
    active, inactive, _fields, version = _prospect_shape(client.db_path, pid)
    assert version == str(migrations.LATEST_SCHEMA_VERSION)

    by_name = {name: (seq, stage, status) for seq, name, stage, status in active}
    # (a) v5 renames were in place.  v7 then retires Area/Resource as source
    # checkpoints, so inspect all rows rather than the active endpoint alone.
    conn = raw_sqlite_connect(client.db_path)
    try:
        all_rows = {row["task_name"]: dict(row) for row in conn.execute(
            "SELECT task_id, task_name, is_active FROM project_tasks WHERE project_id = ?", (pid,))}
    finally:
        conn.close()
    for new_name, old_name in _V5_TO_V4_NAMES.items():
        assert new_name in all_rows
        assert old_name not in all_rows
        assert all_rows[new_name]["task_id"] == ids_before[old_name]
    # (b) merge: ONE row, status = the LESS advanced half (Approved + Ready -> Ready).
    assert by_name["Trap and Seal CoS"][2] == "Ready"
    # (b)/(c) retired, never deleted.
    assert {"Area Definition", "Thickness Estimation", "GRV Inputs", "Resource Assessment",
            "Seal CoS", "Trap CoS", "Well Creation"} <= set(inactive)
    # (d) NEITHER stage is fully approved here (Lead Resource Assessment is
    # still In Progress, Pre-Well Delivery untouched), so both new steps arrive
    # unstarted and nothing is backfilled.
    assert by_name["Well Site Location"][2] == "Not Assigned"
    assert _history(client.db_path, pid, "Migration-Completed") == []
    # (e) stage groups + (f) contiguous 1-12, in template order.
    import workflow
    assert [(seq, name) for seq, name, _stage, _status in active] == [
        (seq, name) for seq, name, _stage in workflow.PIPELINE_TEMPLATES]
    assert {stage for _seq, _name, stage, _st in active if stage in {
            "Lead Assessment", "Risk Analysis", "Pre-Well Delivery"}} == {
        "Lead Assessment", "Risk Analysis", "Pre-Well Delivery"}
    # BP rows remain in their historical 13-27 slots.
    assert [seq for seq, _n, stage, _st in active if stage not in {
        "Lead Assessment", "Risk Analysis", "Pre-Well Delivery"}] == list(range(13, 28))

    # Replay: a second bootstrap against the upgraded file changes nothing.
    after = _prospect_shape(client.db_path, pid)
    _rebootstrap(dbmod, client.db_path)
    assert _prospect_shape(client.db_path, pid) == after


def test_migration_v5_merges_two_approved_halves_into_an_approved_step(client, app_modules):
    """The both-Approved rule, and the merged row's inherited assignee/dates."""
    _, dbmod = app_modules

    pid = create_project(client, "MIGRATE-V5-BOTHAPPROVED-1")
    _make_v4_shaped(client.db_path, statuses={"Trap CoS": "Approved", "Seal CoS": "Approved"})
    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute("UPDATE project_tasks SET assigned_to = 'Employee', priority = 'High', "
                     "actual_start = '2026-01-02', actual_finish = '2026-01-09' "
                     "WHERE task_name = 'Trap CoS'")
        conn.execute("UPDATE project_tasks SET assigned_to = 'Supervisor', priority = 'Low', "
                     "actual_start = '2026-01-01', actual_finish = '2026-01-11' "
                     "WHERE task_name = 'Seal CoS'")
        conn.commit()
    finally:
        conn.close()

    _rebootstrap(dbmod, client.db_path)
    merged = next(t for t in get_tasks(client, pid) if t["task_name"] == "Trap and Seal CoS")
    assert merged["status"] == "Approved"          # both halves Approved
    assert merged["assigned_to"] == "Employee"     # Trap's owner wins
    assert merged["priority"] == "High"            # the MORE urgent of the two
    assert merged["actual_start"] == "2026-01-01"  # earliest start
    assert merged["actual_finish"] == "2026-01-11"  # latest finish (Approved only)


def test_migration_v5_backfills_the_new_steps_on_an_already_completed_lead(client, app_modules):
    """Card-29 constraint 2, the special case where EVERY stage qualifies: a
    lead whose whole prospect phase was already approved must not be reopened by
    two brand-new steps. Both arrive Approved, with no actual dates (nobody did
    the work on a date) and a history event that says exactly why -- and the
    lead stays Completed at 100%."""
    _, dbmod = app_modules

    pid = create_project(client, "MIGRATE-V5-DONE-1")
    _make_v4_shaped(client.db_path,
                    statuses={name: "Approved" for name in _V4_PROSPECT_TEMPLATE})
    _rebootstrap(dbmod, client.db_path)

    after = client.get(f"/api/projects/{pid}").get_json()
    assert after["overall_status"] == "Completed"
    # The lifecycle is complete; the v7 field-derived checkpoint display stays
    # honest about the absent legacy fields, so it reads 8 real items / 12.
    assert client.get(f"/api/projects/{pid}/completion").get_json() == {"percent": round(8 / 12 * 100, 1)}
    tasks = {t["task_name"]: t for t in get_tasks(client, pid)}
    assert tasks["Lead Assessment"]["status"] == "Approved"
    assert tasks["Well Site Location"]["status"] == "Approved"
    assert tasks["Well Site Location"]["actual_start"] is None
    assert tasks["Well Site Location"]["actual_finish"] is None
    events = _history(client.db_path, pid, "Migration-Completed")
    assert [e["task_name"] for e in events] == ["GRV Inputs", "Well Site Location"]
    assert all(e["comment"] ==
               "Migration-completed (backfilled tracked item on an already-completed lead)"
               for e in events)
    assert sum(item["status"] == "Completed" for item in after["tracked_items"]) == 8


def test_migration_v5_backfills_per_stage_so_a_lead_keeps_its_current_stage(client, app_modules):
    """Card-29 constraint 2, the general case: an IN-FLIGHT lead must not slide
    back a board column.

    This lead has finished Lead Identification/Risking entirely and is working
    in Pre-Well Delivery. "GRV Inputs" lands at sequence 3, inside a stage the
    lead has already passed -- so it is backfilled Approved and the derived
    current_stage stays where it was. "Well Site Location" lands in the stage
    still in flight, so it arrives Not Assigned like any unstarted step.

    Backfilling only a WHOLLY approved lead would insert GRV Inputs unstarted
    here, making it the first open row and dragging the card back to
    Lead Assessment -- the forbidden backward move."""
    _, dbmod = app_modules

    pid = create_project(client, "MIGRATE-V5-STAGE-1")
    done = ["Reservoir Area Definition", "Thickness Estimation", "Lead Resource Assessment",
            "Seismic Signature Validation", "Reservoir CoS", "Trap CoS", "Seal CoS",
            "Prospect Evaluation Presentation"]
    _make_v4_shaped(client.db_path, statuses=dict.fromkeys(done, "Approved"))

    before = client.get(f"/api/projects/{pid}").get_json()
    assert before["current_stage"] == "Pre-Well Delivery"
    assert before["display_stage"] == "Pre-Well Delivery"

    _rebootstrap(dbmod, client.db_path)

    after = client.get(f"/api/projects/{pid}").get_json()
    assert after["current_stage"] == "Pre-Well Delivery", "the lead must not slide back a column"
    assert after["display_stage"] == "Pre-Well Delivery"
    tasks = {t["task_name"]: t for t in get_tasks(client, pid)}
    # v5 backfilled GRV before v7 retired it into the consolidated row.
    assert tasks["Lead Assessment"]["status"] == "Approved"
    # Its own stage is still in flight -> unstarted, no event.
    assert tasks["Well Site Location"]["status"] == "Not Assigned"
    assert [e["task_name"] for e in _history(client.db_path, pid, "Migration-Completed")] == \
        ["GRV Inputs"]
    # ... and the tracked item the board draws for it reads done, not open.
    items = {item["label"]: item["status"] for item in after["tracked_items"]}
    assert items["GRV Inputs"] == "In Progress"
    assert items["Well Site Location"] == "In Progress"


def test_migration_v5_backfill_is_scoped_to_the_stage_that_is_finished(client, app_modules):
    """The mirror case: a lead that has finished ONLY its Pre-Well Delivery work
    (unusual, but the vocabulary must not assume an order) backfills Well Site
    Location and leaves GRV Inputs unstarted."""
    _, dbmod = app_modules

    pid = create_project(client, "MIGRATE-V5-STAGE-2")
    _make_v4_shaped(client.db_path, statuses=dict.fromkeys(
        ["Well Creation", "Pre-Drilling Resource Assessment",
         "Staking Moving Tolerance", "Approval to Stake"], "Approved"))

    _rebootstrap(dbmod, client.db_path)

    tasks = {t["task_name"]: t for t in get_tasks(client, pid)}
    assert tasks["Well Site Location"]["status"] == "Approved"
    assert tasks["Lead Assessment"]["status"] == "Not Assigned"
    assert [e["task_name"] for e in _history(client.db_path, pid, "Migration-Completed")] == \
        ["Well Site Location"]


def test_migration_v5_never_ticks_a_confirmation_checkbox_it_was_not_told_to(client, app_modules):
    """MIGRATION-CARE CONSTRAINT: historical checkbox init.

    The redesign introduced confirmation checkboxes that mean "this deliverable
    is filed in the shared folder". For a lead that existed BEFORE them the
    honest value is ABSENT -- nobody has confirmed anything -- and the migration
    must never infer one, least of all from a folder existing on disk (the
    folders are created eagerly by folders.ensure_well_folders, so their
    presence proves nothing about their contents).

    ``staking_well_created`` is the ONE key v5 is allowed to write, and only
    from a stored Approved status (its own test above). This pins the other
    side of that line for every remaining confirmation: after a real v4 -> v5
    upgrade, not one of them exists -- on a lead whose steps were APPROVED as
    well as on an untouched one.
    """
    _, dbmod = app_modules

    done_pid = create_project(client, "MIGRATE-V5-CHECKBOX-DONE")
    open_pid = create_project(client, "MIGRATE-V5-CHECKBOX-OPEN")
    _make_v4_shaped(client.db_path)
    # The dangerous shape, on ONE of the two: a fully approved v4 lead. If
    # anything inferred a confirmation from "this step is finished", it fires
    # here and not on its untouched neighbour.
    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute("UPDATE project_tasks SET status = 'Approved', actual_finish = '2026-01-05' "
                     "WHERE project_id = ? AND is_active = 1", (done_pid,))
        conn.commit()
    finally:
        conn.close()

    _rebootstrap(dbmod, client.db_path)

    # Every confirmation the redesign added, by the step that owns it.
    confirmations = {
        "Lead Assessment": "polygons_surfaces_loaded",
        "Reservoir CoS": "reservoir_slides_loaded",
        "Trap and Seal CoS": "seal_slides_loaded",
        "Seismic Signature Validation": "seismic_slides_loaded",
        "Segmentation Slides": "segmentation_slides_loaded",
        "Approval to Stake": "approval_stake_letter_loaded",
        "Well Site Location": "wellsite_letter_loaded",
    }
    for pid in (done_pid, open_pid):
        tasks = {t["task_name"]: t for t in get_tasks(client, pid)}
        for step, key in confirmations.items():
            fields = client.get(f"/api/tasks/{tasks[step]['task_id']}/dynamic-fields").get_json()
            assert key not in fields, f"{step}.{key} was invented by the migration on project {pid}"
            # Absent, not "0": a migration that wrote a falsy value would still
            # be claiming the user answered the question.
            assert fields.get(key) is None


def test_migration_v5_carries_an_approved_well_creation_onto_approval_to_stake(client, app_modules):
    """Well Creation retires, but an APPROVED one leaves its sign-off behind as
    the staking_well_created checkbox -- an audited insert, guarded on absence."""
    _, dbmod = app_modules

    done_pid = create_project(client, "MIGRATE-V5-WC-DONE-1")
    open_pid = create_project(client, "MIGRATE-V5-WC-OPEN-1")
    conn = raw_sqlite_connect(client.db_path)
    try:
        _make_v4_shaped(client.db_path)
        conn.execute("UPDATE project_tasks SET status = 'Approved' "
                     "WHERE task_name = 'Well Creation' AND project_id = ?", (done_pid,))
        conn.commit()
    finally:
        conn.close()

    _rebootstrap(dbmod, client.db_path)

    def staking_fields(pid):
        task = next(t for t in get_tasks(client, pid) if t["task_name"] == "Approval to Stake")
        return client.get(f"/api/tasks/{task['task_id']}/dynamic-fields").get_json()

    assert staking_fields(done_pid).get("staking_well_created") == "1"
    assert "staking_well_created" not in staking_fields(open_pid)
    # Retired, not deleted, on BOTH records.
    for pid in (done_pid, open_pid):
        assert "Well Creation" not in {t["task_name"] for t in get_tasks(client, pid)}
        assert "Well Creation" in _prospect_shape(client.db_path, pid)[1]
    events = _history(client.db_path, done_pid, "Component Inputs Updated")
    assert any("Well Creation sign-off" in e["comment"] for e in events)


def test_migration_v5_keeps_legacy_cos_data_readable_and_total_cos_unchanged(client, app_modules):
    """The point of merging rather than deleting: a lead scored BEFORE the merge
    reads back the same Total CoS afterwards.

    Its trap_cos_pct / seal_cos_pct sit on the retired halves, and are reachable
    two ways -- v5 COPIES them onto the merged row (so the merged FORM prefills
    instead of opening blank), and every reader keeps a surviving-first ladder
    over the retired buckets as well."""
    _, dbmod = app_modules
    import json

    pid = create_project(client, "MIGRATE-V5-COS-1")
    reservoir = get_task_by_name(client, pid, "Reservoir CoS")
    client.patch(f"/api/tasks/{reservoir['task_id']}/dynamic-fields", json={
        "fields": {"reservoir_cos_rows": json.dumps([{"reservoir_cos_pct": "50"}])}})
    _make_v4_shaped(client.db_path, project_id=pid, legacy_fields=[
        ("Reservoir CoS", "reservoir_cos_rows", json.dumps([{"reservoir_cos_pct": "50"}])),
        ("Trap CoS", "trap_cos_pct", "80"),
        ("Trap CoS", "sarah_quwarah_thickness_ft", "150"),
        ("Seal CoS", "seal_cos_pct", "50"),
        ("Seal CoS", "seal_dip", "0.4"),
    ])
    before = client.get(f"/api/projects/{pid}/detail").get_json()["overview"]["derisking"]
    assert before == "20"          # 0.50 x 0.80 x 0.50

    _rebootstrap(dbmod, client.db_path)

    detail = client.get(f"/api/projects/{pid}/detail").get_json()
    assert detail["overview"]["derisking"] == before, "Total CoS must survive the merge"
    # The retired buckets are still on the payload, untouched...
    assert detail["fields"]["Trap CoS"]["trap_cos_pct"] == "80"
    assert detail["fields"]["Seal CoS"]["seal_cos_pct"] == "50"
    # ... AND the merged step carries a copy, so its form opens filled.
    merged = next(t for t in detail["tasks"] if t["task_name"] == "Trap and Seal CoS")
    merged_fields = client.get(f"/api/tasks/{merged['task_id']}/dynamic-fields").get_json()
    assert merged_fields["trap_cos_pct"] == "80"
    assert merged_fields["seal_cos_pct"] == "50"
    assert merged_fields["sarah_quwarah_thickness_ft"] == "150"
    assert merged_fields["seal_dip"] == "0.4"


def test_migration_v5_recompute_hooks_fire_on_the_merged_step(client, app_modules):
    """The Trap and Seal recompute hooks are re-keyed onto the merged step: a
    save there writes the same EAV keys and recomputes both percentages."""
    _, dbmod = app_modules

    pid = create_project(client, "MIGRATE-V5-HOOKS-1")
    _make_v4_shaped(client.db_path)
    _rebootstrap(dbmod, client.db_path)

    lead_assessment = get_task_by_name(client, pid, "Lead Assessment")
    client.patch(f"/api/tasks/{lead_assessment['task_id']}/dynamic-fields",
                 json={"fields": {"formation_thickness_ft": "100"}})
    merged = get_task_by_name(client, pid, "Trap and Seal CoS")
    resp = client.patch(f"/api/tasks/{merged['task_id']}/dynamic-fields", json={"fields": {
        "sarah_quwarah_thickness_ft": "200",
        "seal_recent_activity_age": "0.95", "seal_dip": "0.8",
        "seal_azimuth_vs_shmax": "0.9", "seal_fault_level_confidence": "0.7",
        "seal_fracture_permeability": "0.5",
    }})
    assert resp.status_code == 200
    stored = client.get(f"/api/tasks/{merged['task_id']}/dynamic-fields").get_json()
    assert stored["trap_cos_pct"], "the Trap hook must have written a percentage"
    assert stored["seal_cos_pct"], "the Seal hook must have written a percentage"


def test_migration_v5_is_a_no_op_on_an_already_migrated_database(client, app_modules):
    """A v4-STAMPED database that already carries the v5 shape (hand-repaired,
    or a half-applied run) upgrades cleanly: every sub-step's guard makes it a
    no-op instead of double-inserting or re-retiring."""
    _, dbmod = app_modules
    import migrations

    pid = create_project(client, "MIGRATE-V5-NOOP-1")
    _make_v4_shaped(client.db_path)
    # Materialize v5 once by hand, then make the runner replay v5 from its
    # v4 stamp.  This is the genuine guard rehearsal; re-stamping a fresh v7
    # shape would make v5 invent rows that no real v5 database carried.
    session = dbmod.new_session()
    try:
        dbmod.begin_write(session)
        migrations._migrate_v5_prospect_template_restructure(session, dbmod.get_engine())
        session.commit()
    finally:
        session.close()
    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute("UPDATE app_settings SET value = '4' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()

    _rebootstrap(dbmod, client.db_path)
    after = _prospect_shape(client.db_path, pid)
    _rebootstrap(dbmod, client.db_path)
    assert _prospect_shape(client.db_path, pid) == after
    assert after[3] == str(migrations.LATEST_SCHEMA_VERSION)


def test_migration_v5_skips_a_project_holding_both_names_of_a_rename(client, app_modules):
    """A project carrying BOTH spellings of a renamed step cannot be renamed --
    project_tasks has UNIQUE(project_id, task_name) -- so the rename's guard
    skips it (the v3 quicklook precedent) and the bootstrap still completes."""
    _, dbmod = app_modules
    import migrations

    pid = create_project(client, "MIGRATE-V5-BOTH-1")
    _make_v4_shaped(client.db_path)
    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute(
            "INSERT INTO project_tasks (project_id, sequence_no, task_name, stage_group, "
            "status, priority, is_active, last_updated) "
            "VALUES (?, 1, 'Area Definition', 'Lead Identification', 'Not Assigned', 'Medium', 1, "
            "datetime('now'))", (pid,))
        conn.commit()
    finally:
        conn.close()

    _rebootstrap(dbmod, client.db_path)
    conn = raw_sqlite_connect(client.db_path)
    try:
        names = {row["task_name"] for row in conn.execute(
            "SELECT task_name FROM project_tasks WHERE project_id = ?", (pid,))}
    finally:
        conn.close()
    # v5's guard left the old spelling for manual reconciliation; v7 consumed
    # the duplicate current spelling into its one canonical row.
    assert {"Reservoir Area Definition", "Area Definition", "Lead Assessment"} <= names
    conn = raw_sqlite_connect(client.db_path)
    try:
        version = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()["value"]
    finally:
        conn.close()
    assert version == str(migrations.LATEST_SCHEMA_VERSION)


def test_new_project_materializes_the_v7_template(client):
    """A FRESH database never runs a migration step: creation materializes the
    9 stored prospect + 15 BP steps straight from PIPELINE_TEMPLATES."""
    import workflow

    pid = create_project(client, "V5-TEMPLATE-1")
    tasks = get_tasks(client, pid)
    assert [(t["sequence_no"], t["task_name"], t["stage_group"]) for t in tasks] == \
        list(workflow.PIPELINE_TEMPLATES)
    prospect = [t for t in tasks if t["stage_group"] in workflow.PROSPECT_STAGES]
    bp = [t for t in tasks if t["stage_group"] in workflow.BP_EXECUTION_STAGES]
    assert (len(prospect), len(bp)) == (9, 15)
    # ... while the board restores the four derived Lead Assessment checkpoints
    # and retains its twelve communicated items.
    row = client.get(f"/api/projects/{pid}").get_json()
    assert len(row["tracked_items"]) == 12
    assert [item["steps"][0] for item in row["tracked_items"][:4]] == ["Lead Assessment"] * 4
    assert [item["steps"][0] for item in row["tracked_items"][4:]] == \
        [t["task_name"] for t in prospect if t["task_name"] != "Lead Assessment"]


# ---------------------------------------------------------------------------
# v6: the machine-derived projects.ground_elevation column
# ---------------------------------------------------------------------------

# The v5 projects shape, column-for-column (models.py before ground_elevation).
_V5_PROJECTS_TABLE = """
    CREATE TABLE projects_v5 (
        project_id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_name TEXT NOT NULL UNIQUE,
        start_date TEXT,
        target_date TEXT,
        business_plan_enabled INTEGER NOT NULL DEFAULT 0,
        business_plan_year INTEGER,
        active_well_enabled INTEGER NOT NULL DEFAULT 0,
        pipeline_type TEXT NOT NULL DEFAULT 'prospect',
        last_updated TEXT,
        archived INTEGER NOT NULL DEFAULT 0,
        lead_folder_path TEXT,
        lead_x REAL,
        lead_y REAL,
        revision INTEGER NOT NULL DEFAULT 0,
        completed_at TEXT
    );
"""
_V5_PROJECTS_COLUMNS = (
    "project_id, project_name, start_date, target_date, business_plan_enabled, "
    "business_plan_year, active_well_enabled, pipeline_type, last_updated, archived, "
    "lead_folder_path, lead_x, lead_y, revision, completed_at"
)


def _projects_shape_and_version(db_path):
    """(projects column set, stamped schema_version, {name: (lead_x, lead_y,
    ground_elevation-or-'absent')}) read raw -- the assertion tuple for the v6
    upgrade-and-replay tests."""
    conn = raw_sqlite_connect(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
        version = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()["value"]
        select = "project_name, lead_x, lead_y" + (
            ", ground_elevation" if "ground_elevation" in columns else "")
        projects = {row["project_name"]:
                    (row["lead_x"], row["lead_y"],
                     row["ground_elevation"] if "ground_elevation" in columns else "absent")
                    for row in conn.execute(f"SELECT {select} FROM projects")}
    finally:
        conn.close()
    return columns, version, projects


def test_migration_v6_adds_ground_elevation_to_a_v5_database_in_place(client, app_modules):
    """Upgrade-and-replay for step 6 (projects.ground_elevation): reshape a
    fresh DB to the v5 form with raw sqlite3 (projects table WITHOUT the
    column, stamped schema_version 5, carrying a real project with
    coordinates), re-bootstrap, and assert the column was added NULL, the
    version stamped current and every row preserved. Then bootstrap once more
    and assert nothing changes."""
    _, dbmod = app_modules
    import migrations

    create_project(client, "MIGRATE-V6-KEEP-1", lead_x="512000.5", lead_y="2903000.25")

    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.executescript(f"""
            {_V5_PROJECTS_TABLE}
            INSERT INTO projects_v5 ({_V5_PROJECTS_COLUMNS})
                SELECT {_V5_PROJECTS_COLUMNS} FROM projects;
            DROP TABLE projects;
            ALTER TABLE projects_v5 RENAME TO projects;
            UPDATE app_settings SET value = '5' WHERE key = 'schema_version';
        """)
        conn.commit()
    finally:
        conn.close()

    _rebootstrap(dbmod, client.db_path)
    columns, version, projects = _projects_shape_and_version(client.db_path)
    assert "ground_elevation" in columns
    assert version == str(migrations.LATEST_SCHEMA_VERSION)
    # The row survived, coordinates intact, and the new column is honestly NULL
    # (machine-derived -- the migration itself never invents a value; the
    # backfill script / save-time fill populate it later).
    assert projects["MIGRATE-V6-KEEP-1"] == (512000.5, 2903000.25, None)

    # Replay: a second bootstrap against the upgraded file changes nothing.
    _rebootstrap(dbmod, client.db_path)
    assert _projects_shape_and_version(client.db_path) == (columns, version, projects)


def test_migration_v6_tolerates_a_hand_altered_database(client, app_modules):
    """A database stamped v5 whose projects table ALREADY has ground_elevation
    (a manual ALTER) must upgrade cleanly: step 6's column-existence guard
    makes it a no-op instead of a duplicate-column error -- and a value already
    stored there is left alone."""
    _, dbmod = app_modules
    import migrations

    pid = create_project(client, "MIGRATE-V6-HAND-1", lead_x="400000", lead_y="2800000")
    conn = raw_sqlite_connect(client.db_path)
    try:
        # Fresh DBs already carry the column; re-stamping v5 simulates a v5
        # database that was hand-ALTERed before this code ran.
        conn.execute("UPDATE projects SET ground_elevation = 321.5 WHERE project_id = ?", (pid,))
        conn.execute("UPDATE app_settings SET value = '5' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()

    _rebootstrap(dbmod, client.db_path)
    columns, version, projects = _projects_shape_and_version(client.db_path)
    assert "ground_elevation" in columns
    assert version == str(migrations.LATEST_SCHEMA_VERSION)
    assert projects["MIGRATE-V6-HAND-1"] == (400000.0, 2800000.0, 321.5)


# ---------------------------------------------------------------------------
# v7: one Lead Assessment lifecycle with four derived checkpoints
# ---------------------------------------------------------------------------

_V7_LEGACY_LEAD_STEPS = (
    ("Area Definition", 1),
    ("Thickness Estimation", 2),
    ("GRV Inputs", 3),
    ("Resource Assessment", 4),
)


def _make_v6_lead_assessment_shape(db_path, project_id, statuses, fields, comments=None):
    """Replace a fresh v7 lead row with the four v6 source rows, raw."""
    conn = raw_sqlite_connect(db_path)
    try:
        conn.execute("DELETE FROM project_tasks WHERE project_id = ? AND task_name = 'Lead Assessment'",
                     (project_id,))
        for name, sequence in _V7_LEGACY_LEAD_STEPS:
            status, assignee, start, finish = statuses[name]
            conn.execute("""
                INSERT INTO project_tasks (
                    project_id, sequence_no, task_name, stage_group, assigned_to, status,
                    actual_start, actual_finish, comments, priority, is_active, last_updated
                ) VALUES (?, ?, ?, 'Lead Assessment', ?, ?, ?, ?, ?, 'Medium', 1, datetime('now'))
            """, (project_id, sequence, name, assignee, status, start, finish,
                  (comments or {}).get(name)))
        for name, key, value in fields:
            task_id = conn.execute(
                "SELECT task_id FROM project_tasks WHERE project_id = ? AND task_name = ?",
                (project_id, name)).fetchone()["task_id"]
            conn.execute("""
                INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
                VALUES (?, ?, ?, datetime('now'))
            """, (task_id, key, value))
        conn.execute("UPDATE app_settings SET value = '6' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()


def _v7_lead_assessment_shape(db_path, project_id):
    conn = raw_sqlite_connect(db_path)
    try:
        rows = [tuple(row) for row in conn.execute("""
            SELECT sequence_no, task_name, status, assigned_to, actual_start, actual_finish, comments, is_active
            FROM project_tasks WHERE project_id = ? ORDER BY sequence_no, task_id
        """, (project_id,))]
        fields = [tuple(row) for row in conn.execute("""
            SELECT pt.task_name, tdf.field_key, tdf.field_value
            FROM project_tasks pt JOIN task_dynamic_fields tdf ON tdf.task_id = pt.task_id
            WHERE pt.project_id = ? ORDER BY pt.task_name, tdf.field_key
        """, (project_id,))]
        history = [tuple(row) for row in conn.execute("""
            SELECT task_name, action_type, new_status, comment
            FROM task_history WHERE project_id = ? ORDER BY history_id
        """, (project_id,))]
        version = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()["value"]
    finally:
        conn.close()
    return rows, fields, history, version


def test_migration_v7_merges_lead_assessment_moves_eav_and_replays(client, app_modules):
    """v7 has one row, preserves the four audit anchors, and moves each key once."""
    _, dbmod = app_modules
    import migrations

    pid = create_project(client, "MIGRATE-V7-LEAD-1")
    _make_v6_lead_assessment_shape(client.db_path, pid, {
        "Area Definition": ("Approved", "Employee", "2026-01-02", "2026-01-03"),
        "Thickness Estimation": ("Ready", "Staff Member", "2026-01-04", None),
        "GRV Inputs": ("In Progress", "Supervisor", "2026-01-05", None),
        "Resource Assessment": ("Approved", "Another", "2026-01-06", "2026-01-07"),
    }, [
        ("Area Definition", "p90_area_km2", "10"),
        ("Thickness Estimation", "formation_thickness_ft", "100"),
        ("GRV Inputs", "grv_p90_thousand_acre_ft", "40"),
        ("Resource Assessment", "lead_piip_gas_mean", "12.5"),
    ])

    _rebootstrap(dbmod, client.db_path)
    rows, fields, history, version = _v7_lead_assessment_shape(client.db_path, pid)
    merged = next(row for row in rows if row[1] == "Lead Assessment")
    assert merged == (1, "Lead Assessment", "In Progress", "Employee", "2026-01-02", None, None, 1)
    assert {row[1] for row in rows if row[7] == 0} >= {name for name, _seq in _V7_LEGACY_LEAD_STEPS}
    assert {(name, key, value) for name, key, value in fields} >= {
        ("Lead Assessment", "p90_area_km2", "10"),
        ("Lead Assessment", "formation_thickness_ft", "100"),
        ("Lead Assessment", "grv_p90_thousand_acre_ft", "40"),
        ("Lead Assessment", "lead_piip_gas_mean", "12.5"),
    }
    assert not any(name in {step for step, _seq in _V7_LEGACY_LEAD_STEPS}
                   for name, _key, _value in fields)
    assert ("Lead Assessment", "Migration-Merged", "In Progress",
            "Merged from 4 Lead Assessment steps (migration v7)") in history
    assert version == str(migrations.LATEST_SCHEMA_VERSION)

    _rebootstrap(dbmod, client.db_path)
    assert _v7_lead_assessment_shape(client.db_path, pid) == (rows, fields, history, version)


def test_v7_prospect_completion_counts_four_derived_lead_checkpoints(client):
    """The v7 row reduction must never change the board's 12-item denominator."""
    pid = create_project(client, "V7-CHECKPOINT-COMPLETION-1")
    task = get_task_by_name(client, pid, "Lead Assessment")
    # v14: unreached tasks are inert; activate the row before saving so the
    # field-completion engine can reconcile it.
    resp = client.post(f"/api/tasks/{task['task_id']}/assign",
                       json={"assigned_to": "Employee", "cascade": False, "revision": task["revision"]})
    assert resp.status_code == 200, resp.get_json()
    task = resp.get_json()["task"]
    response = client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields", json={"fields": {
        "p90_area_km2": "10", "p10_area_km2": "20",
        "reservoir_thickness_ft": "20", "formation_thickness_ft": "100",
        "grv_p90_thousand_acre_ft": "30", "grv_p10_thousand_acre_ft": "40",
        "polygons_surfaces_loaded": "1", "lead_piip_gas_mean": "12.5",
    }})
    assert response.status_code == 200, response.get_json()
    # Fields turn all four derived dots green -- and, since the ASAS owner
    # decision, the save that satisfies the fourth checkpoint auto-approves the
    # single lifecycle row (workflow.AUTO_APPROVE_ON_SAVE_STEPS). The board's
    # denominator is still the four derived dots: completion reads 4/12, the
    # v7 row reduction never turns it into 1/9.
    assert client.get(f"/api/tasks/{task['task_id']}").get_json()["status"] == "Approved"
    assert client.get(f"/api/projects/{pid}/completion").get_json() == {
        "percent": round(4 / 12 * 100, 1)}


@pytest.mark.parametrize("source_comments, expected", [
    ({}, None),
    ({"Thickness Estimation": "One legacy note."}, "One legacy note."),
    ({"Area Definition": "Mapped boundary.", "GRV Inputs": "Volumetrics reviewed."},
     "Area Definition: Mapped boundary.\n\nGRV Inputs: Volumetrics reviewed."),
])
def test_migration_v7_preserves_source_comments_and_allows_new_edits(
        client, app_modules, source_comments, expected):
    """v7 keeps one legacy note verbatim or labels multiple notes by source."""
    _, dbmod = app_modules

    pid = create_project(client, "MIGRATE-V7-COMMENTS-{}".format(len(source_comments)))
    statuses = {name: ("Not Assigned", None, None, None)
                for name, _sequence in _V7_LEGACY_LEAD_STEPS}
    _make_v6_lead_assessment_shape(client.db_path, pid, statuses, [], comments=source_comments)
    _rebootstrap(dbmod, client.db_path)

    task = get_task_by_name(client, pid, "Lead Assessment")
    assert task["comments"] == expected
    conn = raw_sqlite_connect(client.db_path)
    try:
        retired = {row["task_name"]: row["comments"] for row in conn.execute("""
            SELECT task_name, comments FROM project_tasks
            WHERE project_id = ? AND task_name IN ('Area Definition', 'Thickness Estimation',
                                                   'GRV Inputs', 'Resource Assessment')
        """, (pid,))}
    finally:
        conn.close()
    for name, comment in source_comments.items():
        assert retired[name] == comment
    before = _v7_lead_assessment_shape(client.db_path, pid)
    # A normal post-migration component save owns the one current comments box.
    response = client.patch(f"/api/tasks/{task['task_id']}", json={
        "comments": "Current consolidated note.", "revision": task["revision"],
    })
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["task"]["comments"] == "Current consolidated note."
    after = _v7_lead_assessment_shape(client.db_path, pid)
    assert before[0] != after[0]  # the intentional normal comment edit only
    assert next(row for row in after[0] if row[1] == "Lead Assessment")[6] == \
        "Current consolidated note."
    _rebootstrap(dbmod, client.db_path)
    assert _v7_lead_assessment_shape(client.db_path, pid) == after


def test_migration_v7_refuses_a_cross_step_eav_key_collision(client, app_modules):
    """A hand-edited collision must fail before v7 moves any source EAV row."""
    _, dbmod = app_modules
    import migrations

    pid = create_project(client, "MIGRATE-V7-COLLISION-1")
    statuses = {name: ("Not Assigned", None, None, None)
                for name, _sequence in _V7_LEGACY_LEAD_STEPS}
    _make_v6_lead_assessment_shape(client.db_path, pid, statuses, [
        ("Area Definition", "same_key", "first"),
        ("Thickness Estimation", "same_key", "second"),
    ])
    session = dbmod.new_session()
    try:
        with pytest.raises(RuntimeError, match="dynamic field key 'same_key' appears"):
            migrations._migrate_v7_lead_assessment_single_step(session, dbmod.get_engine())
    finally:
        session.rollback()
        session.close()


# ---------------------------------------------------------------------------
# v8: repair fold for projects a stale pre-v7 server created in a v7+ database
# ---------------------------------------------------------------------------

def _insert_stale_pre_v7_project(db_path, name):
    """Insert a prospect project carrying the OLD four-row Lead Assessment
    template, raw -- exactly what a stale pre-v7 server process created inside
    an already-v7-stamped database (dev projects 29/30). Dynamic fields sit on
    the Resource Assessment row, matching the observed corruption."""
    conn = raw_sqlite_connect(db_path)
    try:
        cursor = conn.execute("""
            INSERT INTO projects (project_name, start_date, last_updated, pipeline_type)
            VALUES (?, '2026-07-01', datetime('now'), 'prospect')
        """, (name,))
        pid = cursor.lastrowid
        for sequence, task_name in enumerate(
                ("Area Definition", "Thickness Estimation", "GRV Inputs",
                 "Resource Assessment"), start=1):
            conn.execute("""
                INSERT INTO project_tasks (project_id, sequence_no, task_name, stage_group,
                                           status, priority, is_active, last_updated)
                VALUES (?, ?, ?, 'Lead Assessment', 'In Progress', 'Medium', 1, datetime('now'))
            """, (pid, sequence, task_name))
        # One later step from the old template, to prove v8 re-freezes the
        # sequence slots; the merge itself only needs the four rows above.
        conn.execute("""
            INSERT INTO project_tasks (project_id, sequence_no, task_name, stage_group,
                                       status, priority, is_active, last_updated)
            VALUES (?, 5, 'Reservoir CoS', 'Risk Analysis', 'Not Assigned', 'Low', 1, datetime('now'))
        """, (pid,))
        task_id = conn.execute(
            "SELECT task_id FROM project_tasks WHERE project_id = ? AND task_name = 'Resource Assessment'",
            (pid,)).fetchone()["task_id"]
        for key, value in (("grv_p90_thousand_acre_ft", "40"), ("lead_piip_gas_mean", "12.5")):
            conn.execute("""
                INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
                VALUES (?, ?, ?, datetime('now'))
            """, (task_id, key, value))
        conn.commit()
        return pid
    finally:
        conn.close()


def _stamp_schema_version(db_path, version):
    conn = raw_sqlite_connect(db_path)
    with conn:
        conn.execute("UPDATE app_settings SET value = ? WHERE key = 'schema_version'",
                     (str(version),))
    conn.close()


def test_migration_v8_repairs_a_stale_server_lead_assessment_fold(client, app_modules):
    """v8 re-runs the frozen v7 merge under its own audit name: a project the
    v7 fold never saw (created by a stale pre-v7 server AFTER the database was
    stamped v7) is folded to one Lead Assessment row, its EAV values move, the
    survivors are resequenced, healthy projects are untouched, and a replay
    changes nothing."""
    _, dbmod = app_modules
    import migrations

    healthy = create_project(client, "V8-HEALTHY-1")
    pid = _insert_stale_pre_v7_project(client.db_path, "V8-STALE-1")
    _stamp_schema_version(client.db_path, 7)

    _rebootstrap(dbmod, client.db_path)
    rows, fields, history, version = _v7_lead_assessment_shape(client.db_path, pid)
    merged = next(row for row in rows if row[1] == "Lead Assessment")
    # (sequence_no, name, status, assigned_to, start, finish, comments, is_active)
    assert merged == (1, "Lead Assessment", "In Progress", None, None, None, None, 1)
    assert {row[1] for row in rows if row[7] == 0} == \
        {name for name, _seq in _V7_LEGACY_LEAD_STEPS}
    # v8 re-froze the sequence slots: Reservoir CoS moved from the old 5 to 2.
    assert next(row for row in rows if row[1] == "Reservoir CoS")[0] == 2
    # The EAV values left the corrupted Resource Assessment row wholesale.
    assert set(fields) == {("Lead Assessment", "grv_p90_thousand_acre_ft", "40"),
                           ("Lead Assessment", "lead_piip_gas_mean", "12.5")}
    assert version == str(migrations.LATEST_SCHEMA_VERSION)
    # The one migration event carries the v8 actor and repair comment.
    conn = raw_sqlite_connect(client.db_path)
    try:
        events = [tuple(row) for row in conn.execute("""
            SELECT task_name, action_type, new_status, changed_by, comment
            FROM task_history WHERE project_id = ? ORDER BY history_id
        """, (pid,))]
    finally:
        conn.close()
    assert events == [("Lead Assessment", "Migration-Merged", "In Progress",
                       "System (migration v8)",
                       "Merged from 4 Lead Assessment steps (migration v8 repair)")]
    # The healthy project kept its single row and gained no migration event.
    healthy_rows, _hf, healthy_history, _hv = _v7_lead_assessment_shape(client.db_path, healthy)
    assert [row[1] for row in healthy_rows if row[7] == 1].count("Lead Assessment") == 1
    assert not any(event[1] == "Migration-Merged" for event in healthy_history)

    # Replay: force v8 to run again against the repaired file -- the per-project
    # "Lead Assessment row exists" guard makes it a true no-op.
    before = _v7_lead_assessment_shape(client.db_path, pid)
    _stamp_schema_version(client.db_path, 7)
    _rebootstrap(dbmod, client.db_path)
    assert _v7_lead_assessment_shape(client.db_path, pid) == before


# ---------------------------------------------------------------------------
# v9: priority becomes a stored LEAD-LEVEL attribute (projects.priority)
# ---------------------------------------------------------------------------

def _stored_project_priority(db_path, project_id):
    conn = raw_sqlite_connect(db_path)
    try:
        return conn.execute("SELECT priority FROM projects WHERE project_id = ?",
                            (project_id,)).fetchone()["priority"]
    finally:
        conn.close()


def test_migration_v9_adds_and_backfills_lead_level_priority(client, app_modules):
    """Upgrade-and-replay for step 9: a v8-shaped database (projects table
    WITHOUT the priority column) gets the column, backfilled per project with
    the most urgent priority among its OPEN active tasks -- reproducing what
    the board's old derived lead_priority reported -- and 'Low' when none."""
    _, dbmod = app_modules
    import migrations

    high = create_project(client, "V9-HIGH-1")
    done_high = create_project(client, "V9-DONE-HIGH-1")
    task = get_task_by_name(client, high, "Moving Tolerance")
    assert client.patch(f"/api/tasks/{task['task_id']}/priority",
                        json={"priority": "High"}).status_code == 200

    conn = raw_sqlite_connect(client.db_path)
    with conn:
        # An Approved High task must NOT escalate the lead: finished work is
        # excluded from the backfill, leaving the 'Low' default.
        conn.execute("UPDATE project_tasks SET priority = 'High', status = 'Approved' "
                     "WHERE project_id = ? AND task_name = 'Lead Assessment'", (done_high,))
        conn.execute("ALTER TABLE projects DROP COLUMN priority")
        conn.execute("UPDATE app_settings SET value = '8' WHERE key = 'schema_version'")
    conn.close()

    _rebootstrap(dbmod, client.db_path)
    conn = raw_sqlite_connect(client.db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
        version = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()["value"]
    finally:
        conn.close()
    assert "priority" in columns
    assert version == str(migrations.LATEST_SCHEMA_VERSION)
    assert _stored_project_priority(client.db_path, high) == "High"
    assert _stored_project_priority(client.db_path, done_high) == "Low"

    # Replay safety: the backfill only writes rows still NULL, so a value set
    # after the upgrade survives the step running again.
    conn = raw_sqlite_connect(client.db_path)
    with conn:
        conn.execute("UPDATE projects SET priority = 'Medium' WHERE project_id = ?", (high,))
    conn.close()
    _stamp_schema_version(client.db_path, 8)
    _rebootstrap(dbmod, client.db_path)
    assert _stored_project_priority(client.db_path, high) == "Medium"
    assert _stored_project_priority(client.db_path, done_high) == "Low"


def _v10_business_plan_shape(db_path, project_id):
    conn = raw_sqlite_connect(db_path)
    try:
        fields = [tuple(row) for row in conn.execute("""
            SELECT pt.task_name, f.field_key, f.field_value
            FROM project_tasks pt
            JOIN task_dynamic_fields f ON f.task_id = pt.task_id
            WHERE pt.project_id = ? AND pt.task_name IN (
                'Aramco Picks', 'Flowback Results', 'Quicklook Logs',
                'Final Log Analysis', 'SAD Model', 'SAD Update')
            ORDER BY pt.task_name, f.field_key
        """, (project_id,))]
        formations = [tuple(row) for row in conn.execute("""
            SELECT formation, phase, fluid FROM project_formations
            WHERE project_id = ? ORDER BY id
        """, (project_id,))]
        intervals = [tuple(row) for row in conn.execute("""
            SELECT formation, phase, seq, fluid FROM project_formation_pay_intervals
            WHERE project_id = ? ORDER BY id
        """, (project_id,))]
        history = [tuple(row) for row in conn.execute("""
            SELECT task_name, action_type, old_status, new_status, changed_by, comment
            FROM task_history WHERE project_id = ? ORDER BY history_id
        """, (project_id,))]
        version = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()["value"]
    finally:
        conn.close()
    return fields, formations, intervals, history, version


def test_migration_v10_maps_unambiguous_business_plan_data_and_replays(client, app_modules):
    """Upgrade-and-replay for v10 preserves identifiers/history, maps the four
    retired fluid labels everywhere they are stored (both formation tables and
    the legacy EAV selects), splits the old AAP confirmation, merges Flowback
    confirmations conservatively, and upgrades the one-stage legacy payload."""
    _, dbmod = app_modules
    import migrations

    pid = create_project(client, "V10-BPE-1")
    aramco = get_task_by_name(client, pid, "Aramco Picks")
    flowback = get_task_by_name(client, pid, "Flowback Results")
    # One legacy EAV fluid select per key, one per retired label.
    eav_fluids = [
        ("Quicklook Logs", "quicklook_fluid_type", "Dry", "Dry Hole"),
        ("Final Log Analysis", "final_fluid_type", "Water", "Water Bearing"),
        ("SAD Model", "post_drill_fluid_type", "Condensate", "Oil over Gas"),
        ("SAD Update", "resource_update_fluid_type", "liquid", "Oil"),
    ]
    old_stage = [{
        "_id": "legacy-stage-a",
        "flowback_formation": "SARH",
        "flowback_top_md": "9000",
        "flowback_base_md": "9050",
        "flowback_gas_rate_mmscfd": "15",
        "flowback_water_rate_bwpd": "2",
        "flowback_liquid_rate_bpd": "1",
        "flowback_choke_size_in": "0.5",
        "flowback_fwhp_psi": "2100",
    }]
    conn = raw_sqlite_connect(client.db_path)
    with conn:
        conn.executemany("""
            INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
            VALUES (?, ?, ?, '2026-01-01 00:00:00')
        """, [
            (aramco["task_id"], "aramco_picks_loaded", "1"),
            (flowback["task_id"], "flowback_sheet", "1"),
            (flowback["task_id"], "flowback_slide", "0"),
            (flowback["task_id"], "flowback_dynamic_area_km2", "14.5"),
            (flowback["task_id"], "flowback_dynamic_ogip_bcf", "63"),
            (flowback["task_id"], "flowback_stages_rows", json.dumps(old_stage)),
        ])
        conn.executemany("""
            INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
            VALUES (?, ?, ?, '2026-01-01 00:00:00')
        """, [(get_task_by_name(client, pid, task_name)["task_id"], key, legacy)
              for task_name, key, legacy, _current in eav_fluids])
        conn.execute("""
            INSERT INTO project_formations (project_id, formation, phase, fluid)
            VALUES (?, 'SARH', 'quicklook', 'Dry')
        """, (pid,))
        conn.execute("""
            INSERT INTO project_formations (project_id, formation, phase, fluid)
            VALUES (?, 'QASM', 'final', 'Condensate')
        """, (pid,))
        conn.executemany("""
            INSERT INTO project_formation_pay_intervals
              (project_id, formation, phase, seq, fluid)
            VALUES (?, 'SARH', 'quicklook', ?, ?)
        """, [(pid, 1, "Water"), (pid, 2, "Condensate"), (pid, 3, "Liquid"),
              (pid, 4, "Gas over Water")])
        conn.execute("""
            INSERT INTO task_history
              (task_id, project_id, task_name, action_type, old_status, new_status,
               changed_at, changed_by, comment)
            VALUES (?, ?, 'Flowback Results', 'Legacy Event', 'old', 'new',
                    '2026-01-01 00:00:00', 'Legacy User', 'preserve me')
        """, (flowback["task_id"], pid))
        conn.execute("UPDATE app_settings SET value = '9' WHERE key = 'schema_version'")
    conn.close()

    _rebootstrap(dbmod, client.db_path)
    upgraded = _v10_business_plan_shape(client.db_path, pid)
    fields, formations, intervals, history, version = upgraded
    field_map = {(task, key): value for task, key, value in fields}
    assert version == str(migrations.LATEST_SCHEMA_VERSION)
    assert formations == [("SARH", "quicklook", "Dry Hole"),
                          ("QASM", "final", "Oil over Gas")]
    assert intervals == [
        ("SARH", "quicklook", 1, "Water Bearing"),
        ("SARH", "quicklook", 2, "Oil over Gas"),
        ("SARH", "quicklook", 3, "Oil"),
        # Already current vocabulary: the CASE leaves it alone.
        ("SARH", "quicklook", 4, "Gas over Water"),
    ]
    for task_name, key, _legacy, current in eav_fluids:
        assert field_map[(task_name, key)] == current, (task_name, key)
    assert field_map[("Aramco Picks", "aap_petrel_loaded")] == "1"
    assert field_map[("Aramco Picks", "aap_geoknowledge_loaded")] == "1"
    assert field_map[("Flowback Results", "flowback_shared_confirmed")] == "0"
    migrated_stage = json.loads(field_map[("Flowback Results", "flowback_stages_rows")])[0]
    assert migrated_stage == {
        "id": "legacy-stage-a", "formation": "SARH", "top_md": "9000",
        "base_md": "9050", "dynamic_area_km2": "14.5",
        "dynamic_ogip_bcf": "63", "gas_rate_mmscfd": "15",
        "water_rate_bwpd": "2", "liquid_rate_bpd": "1",
        "choke_size_in": "0.5", "fwhp_psi": "2100",
    }
    assert ("Flowback Results", "Legacy Event", "old", "new", "Legacy User", "preserve me") in history

    _stamp_schema_version(client.db_path, 9)
    _rebootstrap(dbmod, client.db_path)
    assert _v10_business_plan_shape(client.db_path, pid) == upgraded


# ---------------------------------------------------------------------------
# v12: the record-level NUCD Area column
# ---------------------------------------------------------------------------

def _stored_nucd_area(db_path, project_id):
    conn = raw_sqlite_connect(db_path)
    try:
        return conn.execute("SELECT nucd_area FROM projects WHERE project_id = ?",
                            (project_id,)).fetchone()["nucd_area"]
    finally:
        conn.close()


def test_migration_v12_adds_the_nucd_area_column_and_replays(client, app_modules):
    """Upgrade-and-replay for step 12: a v11-shaped database (projects WITHOUT
    nucd_area) gets the nullable column, arriving NULL on every existing record
    -- there is nothing to derive an area from, and inventing one from the
    field or seismic block would be a guess stored as data. A value written
    after the upgrade survives the step running again."""
    _, dbmod = app_modules
    import migrations

    pid = create_project(client, "V12-AREA-1")

    conn = raw_sqlite_connect(client.db_path)
    with conn:
        conn.execute("ALTER TABLE projects DROP COLUMN nucd_area")
        conn.execute("UPDATE app_settings SET value = '11' WHERE key = 'schema_version'")
    conn.close()

    _rebootstrap(dbmod, client.db_path)
    conn = raw_sqlite_connect(client.db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
        version = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()["value"]
    finally:
        conn.close()
    assert "nucd_area" in columns
    assert version == str(migrations.LATEST_SCHEMA_VERSION)
    assert _stored_nucd_area(client.db_path, pid) is None

    # Replay safety: the step only ADDS a missing column, so an area stored
    # after the upgrade is still there when the step runs again.
    conn = raw_sqlite_connect(client.db_path)
    with conn:
        conn.execute("UPDATE projects SET nucd_area = 'North Jafurah' WHERE project_id = ?", (pid,))
    conn.close()
    _stamp_schema_version(client.db_path, 11)
    _rebootstrap(dbmod, client.db_path)
    assert _stored_nucd_area(client.db_path, pid) == "North Jafurah"


def test_migration_v13_normalizes_flowback_stage_rows_and_replays(client, app_modules):
    """v13 converts old prefixed stage keys without losing stable IDs, unknown
    fields, order, or a non-empty BPE/v10 canonical value on a mixed row."""
    _, dbmod = app_modules
    import migrations

    pid = create_project(client, "V13-FLOWBACK-1")
    flowback = get_task_by_name(client, pid, "Flowback Results")
    legacy_rows = [
        {"_id": "legacy-a", "flowback_formation": "SARH", "flowback_top_md": "9000",
         "flowback_gas_rate_mmscfd": "15", "gas_rate_mmscfd": "16", "future_key": "kept"},
        {"id": "current-b", "formation": "QASM", "liquid_rate_bpd": "75"},
        {"flowback_formation": "QWRH", "flowback_water_rate_bwpd": "90"},
        "malformed-list-member",
    ]
    conn = raw_sqlite_connect(client.db_path)
    with conn:
        conn.execute("""
            INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
            VALUES (?, 'flowback_stages_rows', ?, '2026-01-01 00:00:00')
        """, (flowback["task_id"], json.dumps(legacy_rows)))
        conn.execute("UPDATE app_settings SET value = '12' WHERE key = 'schema_version'")
    conn.close()

    _rebootstrap(dbmod, client.db_path)
    conn = raw_sqlite_connect(client.db_path)
    try:
        stored = conn.execute("""
            SELECT field_value FROM task_dynamic_fields
            WHERE task_id = ? AND field_key = 'flowback_stages_rows'
        """, (flowback["task_id"],)).fetchone()["field_value"]
        version = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()["value"]
    finally:
        conn.close()
    expected = [
        {"id": "legacy-a", "formation": "SARH", "top_md": "9000",
         "gas_rate_mmscfd": "16", "future_key": "kept"},
        {"id": "current-b", "formation": "QASM", "liquid_rate_bpd": "75"},
        {"id": "legacy-{}-3".format(flowback["task_id"]), "formation": "QWRH",
         "water_rate_bwpd": "90"},
        "malformed-list-member",
    ]
    assert json.loads(stored) == expected
    assert version == str(migrations.LATEST_SCHEMA_VERSION)

    _stamp_schema_version(client.db_path, 12)
    _rebootstrap(dbmod, client.db_path)
    conn = raw_sqlite_connect(client.db_path)
    try:
        replayed = conn.execute("""
            SELECT field_value FROM task_dynamic_fields
            WHERE task_id = ? AND field_key = 'flowback_stages_rows'
        """, (flowback["task_id"],)).fetchone()["field_value"]
    finally:
        conn.close()
    assert json.loads(replayed) == expected


def test_segmentation_slides_ready_still_reads_pending_approval(client):
    """The one display rule that survived the restructure verbatim."""
    pid = create_project(client, "V5-PENDING-1")
    # Reach the step so it can be assigned and submitted.
    task = reach_task(client, pid, "Segmentation Slides")
    # Card 3S made the shared-folder confirmation a submit REQUIREMENT rather
    # than the submission itself, so it is ticked before asking for review.
    client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields",
                 json={"fields": {"segmentation_slides_loaded": "1"}})
    task = get_task_by_name(client, pid, "Segmentation Slides")
    task = client.post(f"/api/tasks/{task['task_id']}/assign",
                       json={"assigned_to": "Employee", "cascade": False,
                             "revision": task["revision"]}).get_json()["task"]
    resp = client.post(f"/api/tasks/{task['task_id']}/transition",
                       json={"action": "submit", "revision": task["revision"]})
    assert resp.status_code == 200, resp.get_json()
    items = {i["label"]: i["status"] for i in
             client.get(f"/api/projects/{pid}").get_json()["tracked_items"]}
    assert items["Segmentation Slides"] == "Pending Approval"
    assert [label for label, status in items.items() if status == "Pending Approval"] == \
        ["Segmentation Slides"]
