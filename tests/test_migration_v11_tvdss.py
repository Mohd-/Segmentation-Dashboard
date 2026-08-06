"""Card 3H -- migration v11 stores every TVDSS as a magnitude.

TVDSS was the one field this application let through signed, because a horizon
above the datum legitimately reads negative in the field. The card reverses
that: storage becomes the magnitude and the prior sign survives only as an
Audit Trail event, which is the point of the migration -- once the row holds
|x| there is nowhere else the original could be recovered from.

These tests write NEGATIVE values straight into the database (the API now
refuses them, which is the other half of the card) and then run the real
migration step against that database, so what is exercised is the shipped
function rather than a re-implementation of it.
"""
from __future__ import annotations

import sqlite3

import migrations
from conftest import create_project, get_task_by_name, raw_sqlite_connect

EVENT = "TVDSS Sign Normalized"


def _conn(client):
    return raw_sqlite_connect(client.db_path)


def _seed_negative_formation(client, project_id, top, base):
    conn = _conn(client)
    with conn:
        conn.execute(
            "INSERT INTO project_formations (project_id, formation, phase, "
            "top_tvdss_ft, base_tvdss_ft, updated_at) "
            "VALUES (?, 'SARH', 'quicklook', ?, ?, '2026-01-01 00:00:00')",
            (project_id, top, base),
        )
        conn.execute(
            "INSERT INTO project_formation_pay_intervals (project_id, formation, phase, "
            "seq, top_tvdss_ft, base_tvdss_ft, updated_at) "
            "VALUES (?, 'SARH', 'quicklook', 1, ?, ?, '2026-01-01 00:00:00')",
            (project_id, top - 5, base - 5),
        )
    conn.close()


def _seed_negative_lead_reading(client, project_id, value):
    task = get_task_by_name(client, project_id, "Lead Assessment")
    conn = _conn(client)
    with conn:
        conn.execute(
            "INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at) "
            "VALUES (?, 'top_formation_tvdss_ft', ?, '2026-01-01 00:00:00') "
            "ON CONFLICT(task_id, field_key) DO UPDATE SET field_value = excluded.field_value",
            (task["task_id"], value),
        )
    conn.close()
    return task["task_id"]


def _run_migration(client):
    """Run the real v11 step against the test database.

    The shipped function, not a re-implementation of it -- a migration test
    that paraphrases the migration proves nothing about what will run.
    """
    import db as db_module

    session = db_module.get_session()
    with db_module.write_transaction(session):
        migrations._migrate_v11_tvdss_positive(session, db_module.get_engine())


def _formation_depths(client, project_id):
    conn = _conn(client)
    try:
        rows = conn.execute(
            "SELECT top_tvdss_ft, base_tvdss_ft FROM project_formations "
            "WHERE project_id = ?", (project_id,)).fetchall()
        intervals = conn.execute(
            "SELECT top_tvdss_ft, base_tvdss_ft FROM project_formation_pay_intervals "
            "WHERE project_id = ?", (project_id,)).fetchall()
        return [tuple(row) for row in rows], [tuple(row) for row in intervals]
    finally:
        conn.close()


def _events(client, project_id):
    conn = _conn(client)
    try:
        return [dict(row) for row in conn.execute(
            "SELECT task_name, old_status, new_status, changed_by, comment "
            "FROM task_history WHERE project_id = ? AND action_type = ? "
            "ORDER BY history_id", (project_id, EVENT))]
    finally:
        conn.close()


def test_negative_depths_become_magnitudes_and_the_old_sign_is_audited(client):
    pid = create_project(client, "TVDSS-V11-1")
    _seed_negative_formation(client, pid, -120.0, -80.0)

    _run_migration(client)

    formations, intervals = _formation_depths(client, pid)
    assert formations == [(120.0, 80.0)]
    assert intervals == [(125.0, 85.0)]

    events = _events(client, pid)
    assert len(events) == 4, "one event per converted value, not one per row"
    assert {event["changed_by"] for event in events} == {"migration:v11"}
    # The PRIOR signed value is the payload: after the update the row itself no
    # longer carries it, so this event is the only record of what was entered.
    assert sorted(float(event["old_status"]) for event in events) == [-125.0, -120.0, -85.0, -80.0]
    assert sorted(float(event["new_status"]) for event in events) == [80.0, 85.0, 120.0, 125.0]
    assert "prior signed value" in events[0]["comment"]


def test_a_replay_converts_nothing_and_writes_no_second_event(client):
    pid = create_project(client, "TVDSS-V11-2")
    _seed_negative_formation(client, pid, -120.0, -80.0)

    _run_migration(client)
    first = _formation_depths(client, pid)
    assert len(_events(client, pid)) == 4

    _run_migration(client)
    assert _formation_depths(client, pid) == first
    assert len(_events(client, pid)) == 4, "the step selects only still-negative rows"


def test_values_already_positive_are_left_alone(client):
    pid = create_project(client, "TVDSS-V11-3")
    conn = _conn(client)
    with conn:
        conn.execute(
            "INSERT INTO project_formations (project_id, formation, phase, "
            "top_tvdss_ft, base_tvdss_ft, updated_at) "
            "VALUES (?, 'SARH', 'quicklook', 10500, 10620, '2026-01-01 00:00:00')",
            (pid,))
    conn.close()

    _run_migration(client)

    formations, _ = _formation_depths(client, pid)
    assert formations == [(10500.0, 10620.0)]
    assert _events(client, pid) == [], "nothing changed, so nothing is claimed to have"


def test_the_leads_own_tvdss_reading_is_converted_too(client):
    """It lives in the EAV, where every value is text, so the step filters on a
    leading minus rather than a numeric comparison."""
    pid = create_project(client, "TVDSS-V11-4")
    task_id = _seed_negative_lead_reading(client, pid, "-1450")

    _run_migration(client)

    conn = _conn(client)
    try:
        value = conn.execute(
            "SELECT field_value FROM task_dynamic_fields "
            "WHERE task_id = ? AND field_key = 'top_formation_tvdss_ft'",
            (task_id,)).fetchone()["field_value"]
    finally:
        conn.close()
    assert value == "1450", "a whole number does not gain a decimal point"

    events = _events(client, pid)
    assert len(events) == 1
    assert (events[0]["old_status"], events[0]["new_status"]) == ("-1450", "1450")


def test_unparseable_text_is_left_exactly_as_it_is(client):
    """This step converts signs. It is not a data cleaner, and a value it
    cannot read is not a value it should rewrite."""
    pid = create_project(client, "TVDSS-V11-5")
    task_id = _seed_negative_lead_reading(client, pid, "-not a number")

    _run_migration(client)

    conn = _conn(client)
    try:
        value = conn.execute(
            "SELECT field_value FROM task_dynamic_fields "
            "WHERE task_id = ? AND field_key = 'top_formation_tvdss_ft'",
            (task_id,)).fetchone()["field_value"]
    finally:
        conn.close()
    assert value == "-not a number"
    assert _events(client, pid) == []


def test_the_step_is_registered_at_the_current_schema_version(client):
    versions = [version for version, _fn in migrations.MIGRATIONS]
    assert versions == sorted(versions), "steps run in ascending order"
    assert (11, migrations._migrate_v11_tvdss_positive) in migrations.MIGRATIONS
    assert migrations.LATEST_SCHEMA_VERSION == 11
