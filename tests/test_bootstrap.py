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

from conftest import create_project, get_task_by_name, get_tasks, raw_sqlite_connect


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
    """Reshape a fresh (v5) database back to the v4 prospect form, raw.

    A FRESH database is created straight from the v5 template, so the three
    rows v5 adds are simply there and the three it retires never existed. This
    therefore DELETES the added rows (taking their EAV/history with them) and
    REINSTATES the retired ones as active v4 rows -- the same "reinstate the
    old shape, raw" idiom _make_v3_shaped uses for v4. Then it renames the five
    renamed steps back, restores the v4 numbering/stage vocabulary, applies
    ``statuses`` ({v4 task_name: status}, all projects) and ``legacy_fields``
    ((task_name, field_key, value), scoped to ``project_id`` when given), and
    stamps schema_version 4.
    """
    conn = raw_sqlite_connect(db_path)
    try:
        marks = ",".join("?" for _ in _V5_ADDED_ROWS)
        conn.execute(f"DELETE FROM task_dynamic_fields WHERE task_id IN "
                     f"(SELECT task_id FROM project_tasks WHERE task_name IN ({marks}))",
                     _V5_ADDED_ROWS)
        conn.execute(f"DELETE FROM task_history WHERE task_id IN "
                     f"(SELECT task_id FROM project_tasks WHERE task_name IN ({marks}))",
                     _V5_ADDED_ROWS)
        conn.execute(f"DELETE FROM project_tasks WHERE task_name IN ({marks})", _V5_ADDED_ROWS)
        # Reinstate the three rows v5 retires, as ACTIVE v4 rows on every project.
        for name in ("Trap CoS", "Seal CoS", "Well Creation"):
            sequence_no, stage_group = _V4_PROSPECT_TEMPLATE[name]
            conn.execute(
                "INSERT INTO project_tasks (project_id, sequence_no, task_name, stage_group, "
                "status, priority, is_active, last_updated) "
                "SELECT project_id, ?, ?, ?, 'Not Assigned', 'Low', 1, datetime('now') "
                "FROM projects", (sequence_no, name, stage_group))
        for new_name, old_name in _V5_TO_V4_NAMES.items():
            conn.execute("UPDATE project_tasks SET task_name = ? WHERE task_name = ?",
                         (old_name, new_name))
        for name, (sequence_no, stage_group) in _V4_PROSPECT_TEMPLATE.items():
            conn.execute("UPDATE project_tasks SET sequence_no = ?, stage_group = ?, is_active = 1 "
                         "WHERE task_name = ?", (sequence_no, stage_group, name))
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
    # (a) renames -- in place, so the row (and its EAV/history/folder) followed.
    ids_after = {t["task_name"]: t["task_id"] for t in get_tasks(client, pid)}
    for new_name, old_name in _V5_TO_V4_NAMES.items():
        assert new_name in by_name
        assert old_name not in by_name
        assert ids_after[new_name] == ids_before[old_name]
    # (b) merge: ONE row, status = the LESS advanced half (Approved + Ready -> Ready).
    assert by_name["Trap and Seal CoS"][2] == "Ready"
    # (b)/(c) retired, never deleted.
    assert inactive == ["Seal CoS", "Trap CoS", "Well Creation"]
    # (d) NEITHER stage is fully approved here (Lead Resource Assessment is
    # still In Progress, Pre-Well Delivery untouched), so both new steps arrive
    # unstarted and nothing is backfilled.
    assert by_name["GRV Inputs"][2] == "Not Assigned"
    assert by_name["Well Site Location"][2] == "Not Assigned"
    assert _history(client.db_path, pid, "Migration-Completed") == []
    # (e) stage groups + (f) contiguous 1-12, in template order.
    assert [name for _seq, name, _stage, _status in active][:12] == _V5_PROSPECT_STEPS
    assert [seq for seq, _n, _s, _st in active][:12] == list(range(1, 13))
    assert {stage for _seq, name, stage, _st in active if name in _V5_PROSPECT_STEPS} == {
        "Lead Assessment", "Risk Analysis", "Pre-Well Delivery"}
    # BP rows untouched: still 13-27, still their own groups.
    assert [seq for seq, _n, _s, _st in active] == list(range(1, 28))

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
    before = client.get(f"/api/projects/{pid}").get_json()
    assert before["overall_status"] == "Completed"
    assert client.get(f"/api/projects/{pid}/completion").get_json() == {"percent": 100.0}

    _rebootstrap(dbmod, client.db_path)

    after = client.get(f"/api/projects/{pid}").get_json()
    assert after["overall_status"] == "Completed"
    assert client.get(f"/api/projects/{pid}/completion").get_json() == {"percent": 100.0}
    tasks = {t["task_name"]: t for t in get_tasks(client, pid)}
    for name in ("GRV Inputs", "Well Site Location"):
        assert tasks[name]["status"] == "Approved"
        assert tasks[name]["actual_start"] is None
        assert tasks[name]["actual_finish"] is None
    events = _history(client.db_path, pid, "Migration-Completed")
    assert [e["task_name"] for e in events] == ["GRV Inputs", "Well Site Location"]
    assert all(e["comment"] ==
               "Migration-completed (backfilled tracked item on an already-completed lead)"
               for e in events)
    # And every tracked item now reads Completed -- the pre-v5 board could only
    # ever show 10/12 for this lead (two items had no step to complete).
    assert {item["status"] for item in after["tracked_items"]} == {"Completed"}


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
    # Its own stage was finished -> backfilled Approved, with the event.
    assert tasks["GRV Inputs"]["status"] == "Approved"
    assert tasks["GRV Inputs"]["actual_start"] is None
    assert tasks["GRV Inputs"]["actual_finish"] is None
    # Its own stage is still in flight -> unstarted, no event.
    assert tasks["Well Site Location"]["status"] == "Not Assigned"
    assert [e["task_name"] for e in _history(client.db_path, pid, "Migration-Completed")] == \
        ["GRV Inputs"]
    # ... and the tracked item the board draws for it reads done, not open.
    items = {item["label"]: item["status"] for item in after["tracked_items"]}
    assert items["GRV Inputs"] == "Completed"
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
    assert tasks["GRV Inputs"]["status"] == "Not Assigned"
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
        "Resource Assessment": "polygons_surfaces_loaded",
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

    thickness = get_task_by_name(client, pid, "Thickness Estimation")
    client.patch(f"/api/tasks/{thickness['task_id']}/dynamic-fields",
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
    before = _prospect_shape(client.db_path, pid)

    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute("UPDATE app_settings SET value = '4' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()

    _rebootstrap(dbmod, client.db_path)
    assert _prospect_shape(client.db_path, pid) == before
    assert before[3] == str(migrations.LATEST_SCHEMA_VERSION)


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
    names = {t["task_name"] for t in get_tasks(client, pid)}
    assert {"Area Definition", "Reservoir Area Definition"} <= names
    conn = raw_sqlite_connect(client.db_path)
    try:
        version = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()["value"]
    finally:
        conn.close()
    assert version == str(migrations.LATEST_SCHEMA_VERSION)


def test_new_project_materializes_the_v5_template(client):
    """A FRESH database never runs a migration step: creation materializes the
    12 prospect + 15 BP steps straight from PIPELINE_TEMPLATES."""
    import workflow

    pid = create_project(client, "V5-TEMPLATE-1")
    tasks = get_tasks(client, pid)
    assert [(t["sequence_no"], t["task_name"], t["stage_group"]) for t in tasks] == \
        list(workflow.PIPELINE_TEMPLATES)
    prospect = [t for t in tasks if t["stage_group"] in workflow.PROSPECT_STAGES]
    bp = [t for t in tasks if t["stage_group"] in workflow.BP_EXECUTION_STAGES]
    assert (len(prospect), len(bp)) == (12, 15)
    # ... and the board's twelve tracked items are those twelve steps, 1:1.
    row = client.get(f"/api/projects/{pid}").get_json()
    assert [item["steps"][0] for item in row["tracked_items"]] == \
        [t["task_name"] for t in prospect]


def test_segmentation_slides_ready_still_reads_pending_approval(client):
    """The one display rule that survived the restructure verbatim."""
    task = None
    pid = create_project(client, "V5-PENDING-1")
    task = get_task_by_name(client, pid, "Segmentation Slides")
    task = client.post(f"/api/tasks/{task['task_id']}/assign",
                       json={"assignee": "Employee", "cascade": False,
                             "revision": task["revision"]}).get_json()["task"]
    client.post(f"/api/tasks/{task['task_id']}/transition",
                json={"action": "submit", "revision": task["revision"]})
    items = {i["label"]: i["status"] for i in
             client.get(f"/api/projects/{pid}").get_json()["tracked_items"]}
    assert items["Segmentation Slides"] == "Pending Approval"
    assert [label for label, status in items.items() if status == "Pending Approval"] == \
        ["Segmentation Slides"]
