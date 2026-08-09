"""Tests for the Card 1F notification system.

Covers the fan-out policy (workflow/notifications.py), the automation user's
special position, the per-recipient isolation of every read/write, the
idempotence of the two mark-read routes, and the transactional coupling that
makes a notification impossible without the transition it announces.

Identity here is the display-name string, exactly as everywhere else in this
codebase: the tests log in through POST /api/login (SEED_USERS) so the routes
see a real session, because with AUTH_REQUIRED off and no session there is no
addressee at all (main.current_identity).
"""
from __future__ import annotations

import pytest

from conftest import approve_task, create_project, get_tasks, raw_sqlite_connect, reach_task

# Seeded by config.SEED_USERS on every bootstrap.
SUPERVISOR = "Supervisor"
STAFF = "Staff Member"
EMPLOYEE = "Employee"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def login(client, name):
    resp = client.post("/api/login", json={"name": name})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def logout(client):
    assert client.post("/api/logout").status_code == 200


def add_user(client, name, role):
    """Add an extra active user straight into the table.

    config.SEED_USERS is the only seeding path and it is a placeholder list, so
    a second supervisor (the thing a fan-out test needs) has to be inserted
    here rather than configured.
    """
    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute("INSERT INTO users (name, role, is_active, created_at) "
                     "VALUES (?, ?, 1, datetime('now'))", (name, role))
        conn.commit()
    finally:
        conn.close()


def deactivate_user(client, name):
    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute("UPDATE users SET is_active = 0 WHERE name = ?", (name,))
        conn.commit()
    finally:
        conn.close()


def all_notifications(client):
    """Every row in the table, raw -- the only way to see ACROSS recipients
    (the API deliberately cannot)."""
    conn = raw_sqlite_connect(client.db_path)
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM notifications ORDER BY id")]
    finally:
        conn.close()


def first_task(client, project_id):
    return get_tasks(client, project_id)[0]


def assign(client, task_id, assignee):
    resp = client.post(f"/api/tasks/{task_id}/assign",
                       json={"assigned_to": assignee, "cascade": False})
    assert resp.status_code == 200, resp.get_json()
    task = resp.get_json()["task"]
    # These notification tests exercise transition fan-out, not reached-task
    # ordering. A future manual assignment is intentionally only a silent
    # preassignment now, so place it in the desired legacy active state.
    if task["status"] == "Not Assigned":
        import db as dbmod
        import workflow
        session = dbmod.new_session()
        try:
            workflow.lifecycle.activate_task(session, task_id, assignee)
        finally:
            session.close()
        task = client.get(f"/api/tasks/{task_id}").get_json()
    # These tests are about transition fan-out, not the assignment notification
    # itself. Remove any assignment rows so assertions stay focused.
    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute("DELETE FROM notifications WHERE task_id = ? AND event = 'assigned'", (task_id,))
        conn.commit()
    finally:
        conn.close()
    return task


def transition(client, task_id, action, revision=None, expect=200):
    payload = {"action": action}
    if revision is not None:
        payload["revision"] = revision
    resp = client.post(f"/api/tasks/{task_id}/transition", json=payload)
    assert resp.status_code == expect, resp.get_json()
    return resp.get_json()


def feed(client):
    resp = client.get("/api/notifications")
    assert resp.status_code == 200
    return resp.get_json()


def ready_task(client, assignee=EMPLOYEE, name="NOTIFY-1"):
    """A project whose first component is assigned to ``assignee`` and has been
    submitted BY them -- the state every approve/return test starts from."""
    pid = create_project(client, name)
    task = first_task(client, pid)
    assign(client, task["task_id"], assignee)
    login(client, assignee)
    transition(client, task["task_id"], "submit")
    logout(client)
    return pid, task["task_id"]


# ---------------------------------------------------------------------------
# fan-out: submit -> the supervisors, minus the actor
# ---------------------------------------------------------------------------

def test_submit_notifies_every_active_supervisor_except_the_actor(client):
    add_user(client, "Second Supervisor", "supervisor")
    pid = create_project(client, "NOTIFY-SUBMIT-1")
    task = first_task(client, pid)
    assign(client, task["task_id"], EMPLOYEE)

    login(client, EMPLOYEE)
    transition(client, task["task_id"], "submit")

    rows = all_notifications(client)
    assert sorted(row["recipient"] for row in rows) == ["Second Supervisor", "Supervisor"]
    assert {row["event"] for row in rows} == {"submitted"}
    # Staff are not approvers, so they are not told; neither is the actor.
    assert STAFF not in {row["recipient"] for row in rows}
    assert EMPLOYEE not in {row["recipient"] for row in rows}
    # The snapshot fields describe the event, and the message names all three
    # parties of it.
    row = rows[0]
    assert row["actor"] == EMPLOYEE
    assert row["task_name"] == task["task_name"]
    assert row["project_name"] == "NOTIFY-SUBMIT-1"
    assert row["message"] == f"{EMPLOYEE} submitted {task['task_name']} on NOTIFY-SUBMIT-1"
    assert row["read_at"] is None


def test_a_supervisor_submitting_their_own_work_is_not_notified_of_it(client):
    add_user(client, "Second Supervisor", "supervisor")
    pid = create_project(client, "NOTIFY-SUBMIT-2")
    task = first_task(client, pid)
    assign(client, task["task_id"], SUPERVISOR)

    login(client, SUPERVISOR)
    transition(client, task["task_id"], "submit")

    assert [row["recipient"] for row in all_notifications(client)] == ["Second Supervisor"]


def test_submit_skips_deactivated_supervisors(client):
    add_user(client, "Second Supervisor", "supervisor")
    deactivate_user(client, "Second Supervisor")
    pid = create_project(client, "NOTIFY-SUBMIT-3")
    task = first_task(client, pid)
    assign(client, task["task_id"], EMPLOYEE)

    login(client, EMPLOYEE)
    transition(client, task["task_id"], "submit")

    assert [row["recipient"] for row in all_notifications(client)] == [SUPERVISOR]


# ---------------------------------------------------------------------------
# fan-out: approve / return -> the assignee
# ---------------------------------------------------------------------------

def test_approve_notifies_the_assignee_only(client):
    add_user(client, "Second Supervisor", "supervisor")
    _pid, task_id = ready_task(client, name="NOTIFY-APPROVE-1")

    login(client, SUPERVISOR)
    transition(client, task_id, "approve")

    approvals = [row for row in all_notifications(client) if row["event"] == "approved"]
    assert [row["recipient"] for row in approvals] == [EMPLOYEE]
    assert approvals[0]["message"].startswith(f"{SUPERVISOR} approved ")
    # The other supervisor learns nothing from an approval -- only the person
    # whose work it was.
    assert "Second Supervisor" not in {row["recipient"] for row in approvals}


def test_return_notifies_the_assignee(client):
    _pid, task_id = ready_task(client, name="NOTIFY-RETURN-1")

    login(client, SUPERVISOR)
    transition(client, task_id, "return")

    returns = [row for row in all_notifications(client) if row["event"] == "returned"]
    assert [row["recipient"] for row in returns] == [EMPLOYEE]
    assert "returned for update" in returns[0]["message"]


def test_approve_by_the_assignee_themselves_notifies_nobody(client):
    """A supervisor who owns and submits the step and then approves it is both
    actor and assignee: the recipient resolves to the actor, so no row."""
    pid = create_project(client, "NOTIFY-SELF-1")
    task = first_task(client, pid)
    assign(client, task["task_id"], SUPERVISOR)
    login(client, SUPERVISOR)
    transition(client, task["task_id"], "submit")
    transition(client, task["task_id"], "approve")

    # The submit told nobody (the only supervisor was the actor), and the
    # approve's recipient was the actor too.
    assert all_notifications(client) == []


def test_approve_of_an_unassigned_component_notifies_nobody(client):
    """An unassigned step driven to Ready programmatically; a blank
    recipient must produce NO row rather than an empty-string one."""
    pid = create_project(client, "NOTIFY-UNASSIGNED-1")
    task = first_task(client, pid)
    import db as dbmod
    import workflow
    session = dbmod.new_session()
    try:
        workflow.save_task(session, task["task_id"], {"status": "Ready"})
    finally:
        session.close()
    task = client.get(f"/api/tasks/{task['task_id']}").get_json()
    assert (task["assigned_to"] or "") == ""

    login(client, SUPERVISOR)
    transition(client, task["task_id"], "approve")

    assert all_notifications(client) == []


def test_approve_skips_an_assignee_who_has_since_been_deactivated(client):
    _pid, task_id = ready_task(client, name="NOTIFY-GONE-1")
    deactivate_user(client, EMPLOYEE)

    login(client, SUPERVISOR)
    transition(client, task_id, "approve")

    assert [row["event"] for row in all_notifications(client)] == ["submitted"]


# ---------------------------------------------------------------------------
# the 'System' automation identity
# ---------------------------------------------------------------------------

def test_system_walk_notifies_nobody_when_system_owns_the_step(client, app_modules):
    """The auto-complete walk assigns the step to System, then submits and
    approves as System. The submit is suppressed (an automated submit is not a
    request for approval) and the approve's recipient IS the actor -- so a
    fully automated completion is silent."""
    _main, dbmod = app_modules
    import workflow

    pid = create_project(client, "NOTIFY-SYSTEM-1")
    task = first_task(client, pid)

    session = dbmod.new_session()
    try:
        assert workflow.ensure_system_user(session) is not None
        workflow.ensure_task_approved(session, task["task_id"], workflow.SYSTEM_USER)
    finally:
        session.close()

    assert all_notifications(client) == []
    # ... and the step really was driven to Approved (the walk ran).
    assert first_task(client, pid)["status"] == "Approved"


def test_system_approval_of_a_human_owned_step_notifies_that_human(client, app_modules):
    """System approving work a PERSON owns is news for that person: the
    assignee branch applies unchanged, because System is only ever excluded as
    a RECIPIENT, never as an actor whose approvals matter."""
    _main, dbmod = app_modules
    import workflow

    _pid, task_id = ready_task(client, name="NOTIFY-SYSTEM-2")

    session = dbmod.new_session()
    try:
        workflow.ensure_system_user(session)
        workflow.transition_task(session, task_id, "approve",
                                 changed_by=workflow.SYSTEM_USER)
    finally:
        session.close()

    approvals = [row for row in all_notifications(client) if row["event"] == "approved"]
    assert [(row["recipient"], row["actor"]) for row in approvals] == [(EMPLOYEE, "System")]


def test_system_is_never_a_recipient_even_though_it_is_a_supervisor(client, app_modules):
    """'System' is seeded as a SUPERVISOR so no role gate can block an
    automated walk. It must still never appear in a submit fan-out."""
    _main, dbmod = app_modules
    import workflow

    session = dbmod.new_session()
    try:
        assert workflow.ensure_system_user(session)["role"] == "supervisor"
    finally:
        session.close()

    pid = create_project(client, "NOTIFY-SYSTEM-3")
    task = first_task(client, pid)
    assign(client, task["task_id"], EMPLOYEE)
    login(client, EMPLOYEE)
    transition(client, task["task_id"], "submit")

    assert [row["recipient"] for row in all_notifications(client)] == [SUPERVISOR]


# ---------------------------------------------------------------------------
# reads are scoped to the caller -- always
# ---------------------------------------------------------------------------

def test_the_feed_shows_only_the_callers_own_rows_newest_first(client):
    add_user(client, "Second Supervisor", "supervisor")
    pid = create_project(client, "NOTIFY-FEED-1")
    tasks = get_tasks(client, pid)
    for task in tasks[:2]:
        task = reach_task(client, pid, task["task_name"])
        assign(client, task["task_id"], EMPLOYEE)
        login(client, EMPLOYEE)
        transition(client, task["task_id"], "submit")
        logout(client)
        approve_task(client, task["task_id"])

    login(client, SUPERVISOR)
    payload = feed(client)
    assert payload["unread_count"] == 2
    assert [row["recipient"] for row in payload["notifications"]] == [SUPERVISOR, SUPERVISOR]
    # Newest first: the second submit leads.
    assert [row["task_name"] for row in payload["notifications"]] == \
        [tasks[1]["task_name"], tasks[0]["task_name"]]
    # The navigation payload the bell needs: the project to open and the board
    # to open it on (read live from projects, not stored on the row).
    assert payload["notifications"][0]["project_id"] == pid
    assert payload["notifications"][0]["pipeline_type"] == "prospect"

    # The employee's own bell is empty -- they were the actor, never a recipient.
    logout(client)
    login(client, EMPLOYEE)
    assert feed(client) == {"notifications": [], "unread_count": 0}


def test_an_anonymous_request_gets_an_empty_feed_not_someone_elses(client):
    pid = create_project(client, "NOTIFY-ANON-1")
    task = first_task(client, pid)
    assign(client, task["task_id"], EMPLOYEE)
    login(client, EMPLOYEE)
    transition(client, task["task_id"], "submit")
    logout(client)

    # AUTH_REQUIRED is off, so this call succeeds -- with nothing in it.
    assert feed(client) == {"notifications": [], "unread_count": 0}


def test_marking_someone_elses_notification_read_is_a_400(client):
    add_user(client, "Second Supervisor", "supervisor")
    pid = create_project(client, "NOTIFY-FOREIGN-1")
    task = first_task(client, pid)
    assign(client, task["task_id"], EMPLOYEE)
    login(client, EMPLOYEE)
    transition(client, task["task_id"], "submit")
    logout(client)

    foreign = [row for row in all_notifications(client)
               if row["recipient"] == "Second Supervisor"][0]

    login(client, SUPERVISOR)
    resp = client.post(f"/api/notifications/{foreign['id']}/read")
    assert resp.status_code == 400
    assert resp.get_json()["detail"] == "Notification not found."
    # An id that exists for nobody answers identically, so the endpoint cannot
    # be used to probe which notification ids exist.
    unknown = client.post("/api/notifications/999999/read")
    assert unknown.status_code == 400
    assert unknown.get_json()["detail"] == "Notification not found."

    # ... and the foreign row is untouched.
    still_unread = [row for row in all_notifications(client) if row["id"] == foreign["id"]][0]
    assert still_unread["read_at"] is None


def test_read_all_never_reaches_across_recipients(client):
    add_user(client, "Second Supervisor", "supervisor")
    pid = create_project(client, "NOTIFY-READALL-SCOPE-1")
    task = first_task(client, pid)
    assign(client, task["task_id"], EMPLOYEE)
    login(client, EMPLOYEE)
    transition(client, task["task_id"], "submit")
    logout(client)

    login(client, SUPERVISOR)
    assert client.post("/api/notifications/read-all").get_json()["unread_count"] == 0

    by_recipient = {row["recipient"]: row["read_at"] for row in all_notifications(client)}
    assert by_recipient[SUPERVISOR] is not None
    assert by_recipient["Second Supervisor"] is None


# ---------------------------------------------------------------------------
# read / read-all: idempotent, never destructive
# ---------------------------------------------------------------------------

def test_mark_read_is_idempotent_and_keeps_the_row(client):
    pid = create_project(client, "NOTIFY-READ-1")
    task = first_task(client, pid)
    assign(client, task["task_id"], EMPLOYEE)
    login(client, EMPLOYEE)
    transition(client, task["task_id"], "submit")
    logout(client)

    login(client, SUPERVISOR)
    payload = feed(client)
    assert payload["unread_count"] == 1
    notification_id = payload["notifications"][0]["id"]

    first = client.post(f"/api/notifications/{notification_id}/read")
    assert first.status_code == 200
    assert first.get_json() == {"ok": True, "unread_count": 0}
    stamped = [row for row in all_notifications(client) if row["id"] == notification_id][0]["read_at"]
    assert stamped is not None

    # Second call: still 200, still 0 unread, and the ORIGINAL read_at stands.
    second = client.post(f"/api/notifications/{notification_id}/read")
    assert second.status_code == 200
    assert second.get_json() == {"ok": True, "unread_count": 0}
    assert [row for row in all_notifications(client)
            if row["id"] == notification_id][0]["read_at"] == stamped

    # The item is still in the feed -- reading is not deleting.
    after = feed(client)
    assert len(after["notifications"]) == 1
    assert after["notifications"][0]["read_at"] == stamped
    assert after["unread_count"] == 0


def test_read_all_is_idempotent_and_reports_how_many_it_marked(client):
    pid = create_project(client, "NOTIFY-READALL-1")
    tasks = get_tasks(client, pid)[:3]
    for task in tasks:
        task = reach_task(client, pid, task["task_name"])
        assign(client, task["task_id"], EMPLOYEE)
        login(client, EMPLOYEE)
        transition(client, task["task_id"], "submit")
        logout(client)
        approve_task(client, task["task_id"])

    login(client, SUPERVISOR)
    assert feed(client)["unread_count"] == 3

    first = client.post("/api/notifications/read-all").get_json()
    assert first == {"ok": True, "marked": 3, "unread_count": 0}

    second = client.post("/api/notifications/read-all").get_json()
    assert second == {"ok": True, "marked": 0, "unread_count": 0}

    # Nothing was removed.
    assert len(feed(client)["notifications"]) == 3


def test_read_all_with_no_session_is_a_harmless_no_op(client):
    pid = create_project(client, "NOTIFY-READALL-ANON-1")
    task = first_task(client, pid)
    assign(client, task["task_id"], EMPLOYEE)
    login(client, EMPLOYEE)
    transition(client, task["task_id"], "submit")
    logout(client)

    assert client.post("/api/notifications/read-all").get_json() == \
        {"ok": True, "marked": 0, "unread_count": 0}
    assert all_notifications(client)[0]["read_at"] is None


def test_unread_count_tracks_arrivals_and_reads(client):
    pid = create_project(client, "NOTIFY-COUNT-1")
    tasks = get_tasks(client, pid)[:2]

    # Reach, assign and submit the first task; it is still In Progress at this
    # point so the employee can submit it.
    task0 = reach_task(client, pid, tasks[0]["task_name"])
    assign(client, task0["task_id"], EMPLOYEE)

    login(client, SUPERVISOR)
    assert feed(client)["unread_count"] == 0
    logout(client)

    login(client, EMPLOYEE)
    transition(client, task0["task_id"], "submit")
    logout(client)
    login(client, SUPERVISOR)
    assert feed(client)["unread_count"] == 1
    logout(client)

    # Approve the first task so the second one becomes reachable.
    approve_task(client, task0["task_id"])
    task1 = reach_task(client, pid, tasks[1]["task_name"])
    assign(client, task1["task_id"], EMPLOYEE)
    login(client, EMPLOYEE)
    transition(client, task1["task_id"], "submit")
    logout(client)
    login(client, SUPERVISOR)
    payload = feed(client)
    assert payload["unread_count"] == 2

    client.post(f"/api/notifications/{payload['notifications'][0]['id']}/read")
    assert feed(client)["unread_count"] == 1


# ---------------------------------------------------------------------------
# the transactional coupling
# ---------------------------------------------------------------------------

def test_a_failed_transition_writes_no_notification(client):
    """The notification rides transition_task's own write transaction. A stale
    revision aborts the whole thing (409), so there must be no notification --
    and no history event either."""
    pid = create_project(client, "NOTIFY-ROLLBACK-1")
    task = first_task(client, pid)
    assign(client, task["task_id"], EMPLOYEE)

    login(client, EMPLOYEE)
    resp = client.post(f"/api/tasks/{task['task_id']}/transition",
                       json={"action": "submit", "revision": 999})
    assert resp.status_code == 409

    assert all_notifications(client) == []
    # The task never moved.
    assert first_task(client, pid)["status"] == "In Progress"


def test_a_refused_transition_writes_no_notification(client):
    """Same guarantee on the validation path: a wrong from-state is a 400
    raised BEFORE the write, so nothing lands."""
    pid = create_project(client, "NOTIFY-ROLLBACK-2")
    task = first_task(client, pid)

    login(client, SUPERVISOR)
    resp = client.post(f"/api/tasks/{task['task_id']}/transition", json={"action": "approve"})
    assert resp.status_code == 400

    assert all_notifications(client) == []


def test_a_submit_blocked_by_its_required_fields_writes_no_notification(client):
    """The REQUIRED_FIELDS_FOR_SUBMIT gate fires inside the transaction, after
    the row was read and before it was written."""
    import workflow

    gated_name = next(iter(workflow.REQUIRED_FIELDS_FOR_SUBMIT))
    pid = create_project(client, "NOTIFY-ROLLBACK-3", pipeline_type="bp")
    task = reach_task(client, pid, gated_name)
    assign(client, task["task_id"], EMPLOYEE)

    login(client, EMPLOYEE)
    resp = client.post(f"/api/tasks/{task['task_id']}/transition", json={"action": "submit"})
    assert resp.status_code == 400
    assert "Cannot submit until these are checked" in resp.get_json()["detail"]

    assert all_notifications(client) == []


# ---------------------------------------------------------------------------
# the domain functions directly (no HTTP, no session cookie)
# ---------------------------------------------------------------------------

def test_domain_helpers_refuse_to_serve_a_blank_or_foreign_identity(client, app_modules):
    _main, dbmod = app_modules
    import workflow

    pid = create_project(client, "NOTIFY-DOMAIN-1")
    task = first_task(client, pid)
    assign(client, task["task_id"], EMPLOYEE)
    login(client, EMPLOYEE)
    transition(client, task["task_id"], "submit")
    logout(client)

    session = dbmod.new_session()
    try:
        assert workflow.list_notifications(session, "") == []
        assert workflow.list_notifications(session, None) == []
        assert workflow.unread_count(session, "") == 0
        assert workflow.mark_all_read(session, "") == 0

        rows = workflow.list_notifications(session, SUPERVISOR)
        assert len(rows) == 1
        with pytest.raises(ValueError, match="Notification not found."):
            workflow.mark_read(session, "", rows[0]["id"])
        with pytest.raises(ValueError, match="Notification not found."):
            workflow.mark_read(session, EMPLOYEE, rows[0]["id"])
        with pytest.raises(ValueError, match="Notification not found."):
            workflow.mark_read(session, SUPERVISOR, "not-an-id")
        assert workflow.unread_count(session, SUPERVISOR) == 1
    finally:
        session.close()


def test_list_notifications_honours_its_limit(client, app_modules):
    _main, dbmod = app_modules
    import workflow

    pid = create_project(client, "NOTIFY-LIMIT-1")
    tasks = get_tasks(client, pid)[:4]
    for task in tasks:
        task = reach_task(client, pid, task["task_name"])
        assign(client, task["task_id"], EMPLOYEE)
        login(client, EMPLOYEE)
        transition(client, task["task_id"], "submit")
        logout(client)
        approve_task(client, task["task_id"])
    logout(client)

    session = dbmod.new_session()
    try:
        assert len(workflow.list_notifications(session, SUPERVISOR)) == 4
        assert len(workflow.list_notifications(session, SUPERVISOR, limit=2)) == 2
        # A nonsense limit falls back to the default rather than raising.
        assert len(workflow.list_notifications(session, SUPERVISOR, limit="lots")) == 4
    finally:
        session.close()
