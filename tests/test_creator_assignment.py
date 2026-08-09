"""Authenticated lead-creator assignment and role-mapping exemptions."""
from __future__ import annotations

from conftest import (approve_task, create_project, get_task_by_name, get_tasks,
                      raw_sqlite_connect)


def _login(client, name="Staff Member"):
    response = client.post("/api/login", json={"name": name})
    assert response.status_code == 200, response.get_json()


def _new_role(app_modules, name):
    import workflow

    _main, dbmod = app_modules
    session = dbmod.new_session()
    try:
        return workflow.domain_roles.create_role(session, name)
    finally:
        session.close()


def _map_task(app_modules, task_name, role_id):
    import workflow

    _main, dbmod = app_modules
    session = dbmod.new_session()
    try:
        workflow.domain_roles.set_task_mapping(session, task_name, role_id)
    finally:
        session.close()


def test_authenticated_creator_preassigned_to_every_unmapped_task(client):
    _login(client)
    project_id = create_project(client, "CREATOR-ALL-1", changed_by="Spoofed")
    tasks = get_tasks(client, project_id)

    assert len(tasks) == 24
    mapped = {"Quicklook Logs", "Final Log Analysis"}
    for task in tasks:
        if task["task_name"] in mapped:
            assert task["assignees"] == []
            assert task["assigned_to"] is None
        else:
            assert task["assigned_to"] == "Staff Member"
            assert task["assignees"] == [{
                "name": "Staff Member",
                "source": "creator",
                "notified": task["task_name"] == "Lead Assessment",
            }]

    first = tasks[0]
    assert first["status"] == "In Progress"
    assert first["actual_start"]
    assert all(task["status"] == "Not Assigned" for task in tasks[1:])
    assert all(task["actual_start"] is None for task in tasks[1:])

    connection = raw_sqlite_connect(client.db_path)
    try:
        notifications = [dict(row) for row in connection.execute(
            "SELECT task_id, recipient, event FROM notifications ORDER BY id"
        ).fetchall()]
    finally:
        connection.close()
    assert notifications == [{
        "task_id": first["task_id"],
        "recipient": "Staff Member",
        "event": "assigned",
    }]

    # The same automatic source survives the normal later-step activation.
    approve_task(client, first["task_id"], assignee="Staff Member")
    second = get_task_by_name(client, project_id, "Reservoir CoS")
    assert second["status"] == "In Progress"
    assert second["assignees"] == [{
        "name": "Staff Member", "source": "creator", "notified": True,
    }]


def test_role_mapped_first_step_exempts_creator(client, app_modules):
    import workflow

    role_id = _new_role(app_modules, "Creation Geo")
    _main, dbmod = app_modules
    session = dbmod.new_session()
    try:
        workflow.domain_roles.add_membership(session, "Employee", role_id)
        workflow.domain_roles.set_task_mapping(session, "Lead Assessment", role_id)
    finally:
        session.close()

    _login(client)
    project_id = create_project(client, "CREATOR-ROLE-1")
    first = get_task_by_name(client, project_id, "Lead Assessment")

    assert first["status"] == "In Progress"
    assert first["assigned_to"] == "Employee"
    assert first["assignees"] == [{
        "name": "Employee", "source": "role", "notified": True,
    }]
    future = get_task_by_name(client, project_id, "Reservoir CoS")
    assert future["assignees"] == [{
        "name": "Staff Member", "source": "creator", "notified": False,
    }]


def test_mapping_added_before_activation_discards_only_creator_source(
        client, app_modules):
    _login(client, "Supervisor")
    project_id = create_project(client, "CREATOR-LATE-MAP-1")
    reservoir = get_task_by_name(client, project_id, "Reservoir CoS")
    trap = get_task_by_name(client, project_id, "Trap and Seal CoS")

    # An explicit preassignment must survive a later task-role mapping.
    response = client.post(f"/api/tasks/{trap['task_id']}/assignees", json={
        "add": ["Employee"],
    })
    assert response.status_code == 200, response.get_json()

    empty_role_id = _new_role(app_modules, "Empty Creation Role")
    _map_task(app_modules, "Reservoir CoS", empty_role_id)
    _map_task(app_modules, "Trap and Seal CoS", empty_role_id)

    reservoir = get_task_by_name(client, project_id, "Reservoir CoS")
    assert reservoir["status"] == "Not Assigned"
    assert reservoir["assigned_to"] is None
    assert reservoir["assignees"] == []

    trap = get_task_by_name(client, project_id, "Trap and Seal CoS")
    assert trap["assignees"] == [{
        "name": "Employee", "source": "manual", "notified": False,
    }]

    # Reaching an empty mapped role never falls back to the removed creator.
    first = get_task_by_name(client, project_id, "Lead Assessment")
    approve_task(client, first["task_id"], assignee="Supervisor")
    reservoir = get_task_by_name(client, project_id, "Reservoir CoS")
    assert reservoir["status"] == "Not Assigned"
    assert reservoir["assignees"] == []


def test_creator_assignment_is_removable_like_manual(client):
    _login(client)
    project_id = create_project(client, "CREATOR-REMOVE-1")
    future = get_task_by_name(client, project_id, "Reservoir CoS")

    response = client.post(f"/api/tasks/{future['task_id']}/assignees", json={
        "remove": ["Staff Member"],
    })
    assert response.status_code == 200, response.get_json()
    task = response.get_json()["task"]
    assert task["status"] == "Not Assigned"
    assert task["assigned_to"] is None
    assert task["assignees"] == []


def test_anonymous_and_in_process_creation_do_not_infer_creator(
        client, app_modules):
    anonymous_id = create_project(client, "CREATOR-ANON-1", changed_by="Employee")
    assert all(task["assignees"] == [] for task in get_tasks(client, anonymous_id))

    import workflow

    _main, dbmod = app_modules
    session = dbmod.new_session()
    try:
        direct_id = workflow.add_project(
            session, "CREATOR-DIRECT-1", changed_by="Staff Member")
    finally:
        session.close()
    assert all(task["assignees"] == [] for task in get_tasks(client, direct_id))


def test_invalid_explicit_creator_is_rejected_atomically(client, app_modules):
    import pytest
    import workflow

    _main, dbmod = app_modules
    session = dbmod.new_session()
    try:
        with pytest.raises(ValueError, match="Unknown or inactive lead creator"):
            workflow.add_project(
                session, "CREATOR-INVALID-1", creator_name="Not A User")
    finally:
        session.close()

    connection = raw_sqlite_connect(client.db_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM projects WHERE project_name = ?",
            ("CREATOR-INVALID-1",),
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_creator_rows_and_creation_event_roll_back_with_project(
        client, app_modules, monkeypatch):
    import pytest
    import workflow

    def fail_history(*_args, **_kwargs):
        raise RuntimeError("history write failed")

    monkeypatch.setattr(workflow.projects, "log_task_event", fail_history)
    _main, dbmod = app_modules
    session = dbmod.new_session()
    try:
        with pytest.raises(RuntimeError, match="history write failed"):
            workflow.add_project(
                session, "CREATOR-ROLLBACK-1", creator_name="Employee")
    finally:
        session.close()

    connection = raw_sqlite_connect(client.db_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM projects WHERE project_name = ?",
            ("CREATOR-ROLLBACK-1",),
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM task_assignees").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM task_history").fetchone()[0] == 0
    finally:
        connection.close()
