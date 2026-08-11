"""Migration v17 -- already-staked leads mature.

Runtime now auto-approves "Well Site Location" and "Pre-Drilling GeoX
Assessment" the moment "Approval to Stake" is approved, so a staked lead
derives Completed and leaves the Segment Maturation board. Leads staked BEFORE
that rule shipped still hold those two steps open; v17 closes them out in
place, with a history event per row and a completed_at stamp where the
prospect set is now fully approved.

These tests build that pre-v17 shape by writing the stake approval straight
into the database (the runtime path would auto-mature, which is exactly what
legacy rows never got) and then run the real migration step against it, so
what is exercised is the shipped function rather than a re-implementation.
"""
from __future__ import annotations

import migrations
from conftest import create_project, get_task_by_name, raw_sqlite_connect, reach_task

ACTOR = "System (migration v17)"
EVENT = "Migration-Completed"
MATURED = ("Well Site Location", "Pre-Drilling GeoX Assessment")


def _conn(client):
    return raw_sqlite_connect(client.db_path)


def _stake_raw(client, project_id):
    """Mark Approval to Stake Approved by direct SQL -- the pre-v17 data shape.

    Deliberately NOT via the lifecycle: the runtime approve path now
    auto-matures the remaining steps, which is precisely what a legacy staked
    lead never received.
    """
    conn = _conn(client)
    with conn:
        conn.execute(
            "UPDATE project_tasks SET status = 'Approved' "
            "WHERE project_id = ? AND task_name = 'Approval to Stake' AND is_active = 1",
            (project_id,),
        )
    conn.close()


def _run_migration(client):
    """Run the real v17 step against the test database.

    The shipped function, not a re-implementation of it -- a migration test
    that paraphrases the migration proves nothing about what will run.
    """
    import db as db_module

    session = db_module.get_session()
    try:
        with db_module.write_transaction(session):
            migrations._migrate_v17_staked_leads_mature(session, db_module.get_engine())
    finally:
        session.close()


def _task_row(client, project_id, task_name):
    conn = _conn(client)
    try:
        return dict(conn.execute(
            "SELECT status, actual_start, actual_finish, revision FROM project_tasks "
            "WHERE project_id = ? AND task_name = ? AND is_active = 1",
            (project_id, task_name)).fetchone())
    finally:
        conn.close()


def _events(client, project_id):
    conn = _conn(client)
    try:
        return [dict(row) for row in conn.execute(
            "SELECT task_name, old_status, new_status, changed_by, comment "
            "FROM task_history WHERE project_id = ? AND action_type = ? "
            "AND changed_by = ? ORDER BY history_id",
            (project_id, EVENT, ACTOR))]
    finally:
        conn.close()


def _project_row(client, project_id):
    conn = _conn(client)
    try:
        return dict(conn.execute(
            "SELECT completed_at, revision FROM projects WHERE project_id = ?",
            (project_id,)).fetchone())
    finally:
        conn.close()


def _staked_and_unstaked_pair(client):
    """One legacy staked lead (post-stake steps open) and one unstaked lead."""
    staked = create_project(client, "V17-Staked")
    reach_task(client, staked, "Approval to Stake")  # approves steps 1-6
    _stake_raw(client, staked)

    unstaked = create_project(client, "V17-Unstaked")
    return staked, unstaked


def test_staked_lead_matures_with_history_and_completed_at(client):
    staked, unstaked = _staked_and_unstaked_pair(client)
    before_revisions = {name: _task_row(client, staked, name)["revision"]
                        for name in MATURED}

    _run_migration(client)

    for name in MATURED:
        row = _task_row(client, staked, name)
        assert row["status"] == "Approved"
        # v5 backfill rule: nobody did the work on a date.
        assert row["actual_finish"] is None
        assert row["revision"] == before_revisions[name] + 1

    events = _events(client, staked)
    assert [event["task_name"] for event in events] == list(MATURED)
    for event in events:
        assert event["new_status"] == "Approved"
        assert event["old_status"] != "Approved"
        assert "matures the remaining prospect steps" in event["comment"]

    project = _project_row(client, staked)
    assert project["completed_at"], "prospect set fully approved -> stamped"
    assert _events(client, unstaked) == []


def test_an_unstaked_lead_is_untouched(client):
    _, unstaked = _staked_and_unstaked_pair(client)
    before = {name: _task_row(client, unstaked, name) for name in MATURED}
    before_project = _project_row(client, unstaked)

    _run_migration(client)

    for name in MATURED:
        assert _task_row(client, unstaked, name) == before[name]
    project = _project_row(client, unstaked)
    assert project["completed_at"] is None
    assert project["revision"] == before_project["revision"]
    assert _events(client, unstaked) == []


def test_a_replay_approves_nothing_and_writes_no_second_event(client):
    staked, _ = _staked_and_unstaked_pair(client)

    _run_migration(client)
    first_events = _events(client, staked)
    first_project = _project_row(client, staked)
    first_rows = {name: _task_row(client, staked, name) for name in MATURED}
    assert len(first_events) == 2

    _run_migration(client)
    assert _events(client, staked) == first_events
    assert _project_row(client, staked) == first_project, \
        "no re-stamp, no revision churn on replay"
    for name in MATURED:
        assert _task_row(client, staked, name) == first_rows[name]


def test_a_partially_open_prospect_set_gets_no_completed_at(client):
    """The sweep mirrors _sync_completed_at: only a FULLY approved prospect
    set is stamped. A hand-shaped lead with an approved stake but an earlier
    step still open matures its two post-stake steps and stays unstamped."""
    pid = create_project(client, "V17-Partial")
    _stake_raw(client, pid)  # earlier prospect steps remain non-Approved

    _run_migration(client)

    for name in MATURED:
        assert _task_row(client, pid, name)["status"] == "Approved"
    assert len(_events(client, pid)) == 2
    assert _project_row(client, pid)["completed_at"] is None


def test_the_step_is_registered_and_versioned(client):
    versions = [version for version, _fn in migrations.MIGRATIONS]
    assert versions == sorted(versions), "steps run in ascending order"
    assert (17, migrations._migrate_v17_staked_leads_mature) in migrations.MIGRATIONS
    assert migrations.LATEST_SCHEMA_VERSION >= 17
