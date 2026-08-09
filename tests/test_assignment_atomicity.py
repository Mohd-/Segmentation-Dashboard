"""Regression coverage for authoritative and atomic task assignment."""
from __future__ import annotations

import threading

import pytest

from conftest import create_project, get_tasks, raw_sqlite_connect


def test_domain_save_cannot_write_legacy_assigned_to(client, app_modules):
    """Internal callers cannot desynchronise the scalar compatibility field."""
    _main, dbmod = app_modules
    import workflow

    project_id = create_project(client, "ASSIGN-SCALAR-GUARD")
    task = get_tasks(client, project_id)[0]

    session = dbmod.new_session()
    try:
        assigned, _ = workflow.lifecycle.update_task_assignees(
            session, task["task_id"], add_names=["Employee"], changed_by="Supervisor")
        assert assigned["assigned_to"] == "Employee"

        with pytest.raises(ValueError, match="assigned_to is a derived field"):
            workflow.save_task(
                session, task["task_id"], {"assigned_to": "Supervisor"})

        unchanged = workflow.get_task(session, task["task_id"])
        assert unchanged["assigned_to"] == "Employee"
        assert [row["name"] for row in unchanged["assignees"]] == ["Employee"]
    finally:
        session.close()


def test_late_concurrent_assignee_add_gets_one_notification(
        client, app_modules, monkeypatch):
    """A request paused before the write lock makes no stale status decision.

    The late thread reaches the call to ``write_transaction`` first and pauses.
    The main thread then adds Employee and activates the task completely.  Once
    resumed, the late thread must re-read ``In Progress`` under the lock and
    notify Staff Member exactly once for their newly added active assignment.
    """
    _main, dbmod = app_modules
    import workflow

    project_id = create_project(client, "ASSIGN-ATOMIC-RACE")
    task_id = get_tasks(client, project_id)[0]["task_id"]

    late_waiting = threading.Event()
    release_late = threading.Event()
    delayed_once = threading.Event()
    real_write_transaction = dbmod.write_transaction

    def delayed_write_transaction(session):
        if (threading.current_thread().name == "late-assignee"
                and not delayed_once.is_set()):
            delayed_once.set()
            late_waiting.set()
            if not release_late.wait(timeout=10):
                raise RuntimeError("Timed out waiting to resume late assignee request.")
        return real_write_transaction(session)

    monkeypatch.setattr(dbmod, "write_transaction", delayed_write_transaction)
    errors = []

    def add_late_assignee():
        session = dbmod.new_session()
        try:
            workflow.lifecycle.update_task_assignees(
                session, task_id, add_names=["Staff Member"], changed_by="Supervisor")
        except Exception as exc:  # pragma: no cover - asserted in main thread
            errors.append(exc)
        finally:
            session.close()

    thread = threading.Thread(target=add_late_assignee, name="late-assignee")
    thread.start()
    assert late_waiting.wait(timeout=10), "late request did not reach the write boundary"

    session = dbmod.new_session()
    try:
        workflow.lifecycle.update_task_assignees(
            session, task_id, add_names=["Employee"], changed_by="Supervisor")
    finally:
        session.close()

    release_late.set()
    thread.join(timeout=10)
    assert not thread.is_alive(), "late assignee request did not finish"
    assert errors == []

    final_task = client.get(f"/api/tasks/{task_id}").get_json()
    assert final_task["status"] == "In Progress"
    assert final_task["assigned_to"] == "Employee"
    assert final_task["assignees"] == [
        {"name": "Employee", "source": "manual", "notified": True},
        {"name": "Staff Member", "source": "manual", "notified": True},
    ]

    conn = raw_sqlite_connect(client.db_path)
    try:
        notifications = [dict(row) for row in conn.execute("""
            SELECT recipient, event FROM notifications
            WHERE task_id = ? ORDER BY recipient, id
        """, (task_id,))]
        activations = conn.execute("""
            SELECT COUNT(*) FROM task_history
            WHERE task_id = ? AND action_type = 'Component Activated'
        """, (task_id,)).fetchone()[0]
    finally:
        conn.close()

    assert notifications == [
        {"recipient": "Employee", "event": "assigned"},
        {"recipient": "Staff Member", "event": "assigned"},
    ]
    assert activations == 1
