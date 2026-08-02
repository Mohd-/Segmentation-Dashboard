"""Card 3D -- the Segmentation Slides approval workflow.

The one tracked item whose completion is still a HUMAN decision. Its employee
has no "Submit for Approval" button: ticking "Segmentation slides are placed in
the shared folder" and saving IS the request for review
(constants.CHECKBOX_SUBMIT_STEPS -> lifecycle.apply_checkbox_submission), and
only a supervisor's Approved click closes the step.

Everything here drives the REAL endpoints (PATCH /api/tasks/<id> with a fields
payload -- exactly what the detail form's Save sends -- and POST
/api/tasks/<id>/transition), so nothing pins an internal call shape.
"""
from __future__ import annotations

from conftest import create_project, get_task_by_name, raw_sqlite_connect

SUPERVISOR = "Supervisor"
EMPLOYEE = "Employee"
STEP = "Segmentation Slides"
BOX = "segmentation_slides_loaded"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def login(client, name):
    resp = client.post("/api/login", json={"name": name})
    assert resp.status_code == 200, resp.get_json()


def save(client, task, fields, expect=200, **extra):
    """PATCH /api/tasks/<id> with a fields payload -- the detail form's Save.

    Sends NO ``status`` key, exactly like the v17 UI.
    """
    body = {"fields": fields, "revision": task["revision"],
            "priority": task.get("priority") or "Medium"}
    body.update(extra)
    resp = client.patch(f"/api/tasks/{task['task_id']}", json=body)
    assert resp.status_code == expect, resp.get_json()
    return resp.get_json().get("task")


def transition(client, task, action, expect=200):
    resp = client.post(f"/api/tasks/{task['task_id']}/transition",
                       json={"action": action, "revision": task["revision"]})
    assert resp.status_code == expect, resp.get_json()
    return resp.get_json().get("task")


def assign(client, task, assignee):
    resp = client.post(f"/api/tasks/{task['task_id']}/assign",
                       json={"assignee": assignee, "cascade": False, "revision": task["revision"]})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["task"]


def history(client, task_id):
    conn = raw_sqlite_connect(client.db_path)
    try:
        rows = conn.execute(
            "SELECT action_type, old_status, new_status, changed_by, comment "
            "FROM task_history WHERE task_id = ? ORDER BY history_id", (task_id,)).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def notifications(client, task_id):
    conn = raw_sqlite_connect(client.db_path)
    try:
        rows = conn.execute(
            "SELECT recipient, actor, event FROM notifications WHERE task_id = ? ORDER BY id",
            (task_id,)).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def tracked_items(client, pid):
    row = client.get(f"/api/projects/{pid}").get_json()
    return {item["label"]: item["status"] for item in row["tracked_items"]}


def slides_task(client, pid):
    return get_task_by_name(client, pid, STEP)


# ---------------------------------------------------------------------------
# The declarative tables
# ---------------------------------------------------------------------------

def test_the_checkbox_submit_table_names_only_manual_approval_steps():
    """A step may be field-COMPLETED or checkbox-SUBMITTED, never both: the
    first drives itself to Approved, the second stops at Ready on purpose."""
    import workflow

    assert workflow.CHECKBOX_SUBMIT_STEPS == {STEP: BOX}
    assert not (set(workflow.CHECKBOX_SUBMIT_STEPS) & set(workflow.FIELD_COMPLETION))
    assert set(workflow.CHECKBOX_SUBMIT_STEPS) <= workflow.FIELD_COMPLETION_MANUAL_APPROVAL_STEPS
    assert set(workflow.CHECKBOX_SUBMIT_STEPS) <= {name for _s, name, _g in workflow.PIPELINE_TEMPLATES}


def test_the_submission_only_ever_moves_a_step_to_ready():
    import workflow

    assert workflow.CHECKBOX_SUBMIT_FROM_STATUSES == frozenset({"Not Assigned", "In Progress"})
    assert workflow.TASK_TRANSITIONS["submit"] == ("In Progress", "Ready")


# ---------------------------------------------------------------------------
# The employee's save = submit
# ---------------------------------------------------------------------------

def test_checked_save_submits_for_review_in_the_same_action(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-SUBMIT-1")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    assert task["status"] == "In Progress"
    login(client, EMPLOYEE)

    saved = save(client, task, {BOX: "1"})

    # The save RESPONSE already carries the post-submit row, so the client
    # adopts the new status/revision instead of a stale pair.
    assert saved["status"] == "Ready"
    assert slides_task(client, pid)["status"] == "Ready"
    assert tracked_items(client, pid)[STEP] == "Pending Approval"
    # One save, one submission, audited under the SAVING USER.
    submits = [row for row in history(client, task["task_id"])
               if row["action_type"] == "Component Submitted"]
    assert len(submits) == 1
    assert submits[0]["changed_by"] == EMPLOYEE
    assert (submits[0]["old_status"], submits[0]["new_status"]) == ("In Progress", "Ready")


def test_the_submission_notifies_the_supervisors(client):
    """It is a REAL request for approval, so the fan-out must fire -- the
    opposite of the field-completion engine's automated walk."""
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-NOTIFY-1")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)

    save(client, task, {BOX: "1"})

    assert notifications(client, task["task_id"]) == \
        [{"recipient": SUPERVISOR, "actor": EMPLOYEE, "event": "submitted"}]


def test_a_second_checked_save_does_not_submit_twice(client):
    """A step already Ready is waiting on a supervisor: re-saving it (fixing a
    typo in the comments) must not file the same review request again."""
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-SUBMIT-2")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)
    pending = save(client, task, {BOX: "1"})

    again = save(client, pending, {BOX: "1"}, comments="second thoughts")

    assert again["status"] == "Ready"
    assert len([row for row in history(client, task["task_id"])
                if row["action_type"] == "Component Submitted"]) == 1
    assert len(notifications(client, task["task_id"])) == 1
    assert tracked_items(client, pid)[STEP] == "Pending Approval"


def test_an_unchecked_save_is_a_draft_and_leaves_the_status_alone(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-DRAFT-1")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)

    saved = save(client, task, {BOX: ""}, comments="still working on the slides")

    assert saved["status"] == "In Progress"
    assert saved["comments"] == "still working on the slides"
    assert notifications(client, task["task_id"]) == []
    assert tracked_items(client, pid)[STEP] == "In Progress"


def test_unticking_never_withdraws_a_pending_submission(client):
    """There is no "withdraw" in the lifecycle, and inventing one here would
    silently cancel a review the supervisor may already be reading."""
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-DRAFT-2")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)
    pending = save(client, task, {BOX: "1"})
    assert pending["status"] == "Ready"

    still_pending = save(client, pending, {BOX: ""})

    assert still_pending["status"] == "Ready"
    assert tracked_items(client, pid)[STEP] == "Pending Approval"


def test_a_checked_save_on_an_unassigned_step_assigns_the_saving_user(client):
    """Assignment is the only door out of "Not Assigned", so the save has to
    name somebody -- the person who ticked the box."""
    login(client, EMPLOYEE)
    pid = create_project(client, "SS-ASSIGN-1")
    task = slides_task(client, pid)
    assert task["status"] == "Not Assigned"

    saved = save(client, task, {BOX: "1"})

    assert (saved["status"], saved["assigned_to"]) == ("Ready", EMPLOYEE)
    assert [row["action_type"] for row in history(client, task["task_id"])] == [
        "Component Inputs Updated", "Component Assigned", "Component Submitted"]


def test_a_checked_save_never_takes_over_someone_elses_step(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-ASSIGN-2")
    task = assign(client, slides_task(client, pid), EMPLOYEE)

    saved = save(client, task, {BOX: "1"})  # saved by the supervisor

    assert (saved["status"], saved["assigned_to"]) == ("Ready", EMPLOYEE)


def test_a_save_that_drives_status_explicitly_stands_the_hook_down(client):
    """PATCH with a ``status`` key is a caller driving status directly (the
    legacy path); the hook reacts to FIELD edits, not to status writes."""
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-EXPLICIT-1")
    task = assign(client, slides_task(client, pid), EMPLOYEE)

    saved = save(client, task, {BOX: "1"}, status="In Progress")

    assert saved["status"] == "In Progress"
    assert not [row for row in history(client, task["task_id"])
                if row["action_type"] == "Component Submitted"]


# ---------------------------------------------------------------------------
# The supervisor's decision
# ---------------------------------------------------------------------------

def test_supervisor_approval_completes_the_tracked_item(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-APPROVE-1")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)
    pending = save(client, task, {BOX: "1"})
    login(client, SUPERVISOR)

    approved = transition(client, pending, "approve")

    assert approved["status"] == "Approved"
    assert approved["actual_finish"]          # the timestamp the card asks for
    assert tracked_items(client, pid)[STEP] == "Completed"
    # WHO approved and WHEN is in the audit trail either way.
    event = [row for row in history(client, task["task_id"])
             if row["action_type"] == "Component Approved"]
    assert len(event) == 1 and event[0]["changed_by"] == SUPERVISOR
    assert notifications(client, task["task_id"])[-1] == \
        {"recipient": EMPLOYEE, "actor": SUPERVISOR, "event": "approved"}


def test_approval_history_records_the_actor_and_a_timestamp(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-APPROVE-2")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)
    pending = save(client, task, {BOX: "1"})
    login(client, SUPERVISOR)
    transition(client, pending, "approve")

    conn = raw_sqlite_connect(client.db_path)
    try:
        row = dict(conn.execute(
            "SELECT changed_by, changed_at FROM task_history "
            "WHERE task_id = ? AND action_type = 'Component Approved'",
            (task["task_id"],)).fetchone())
    finally:
        conn.close()
    assert row["changed_by"] == SUPERVISOR
    assert row["changed_at"]


def test_return_reopens_the_step_with_every_field_intact(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-RETURN-1")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)
    pending = save(client, task, {BOX: "1"}, comments="ready for review")
    login(client, SUPERVISOR)

    returned = transition(client, pending, "return")

    assert returned["status"] == "In Progress"
    assert returned["assigned_to"] == EMPLOYEE
    assert returned["comments"] == "ready for review"
    assert client.get(f"/api/tasks/{task['task_id']}/dynamic-fields").get_json()[BOX] == "1"
    assert tracked_items(client, pid)[STEP] == "In Progress"
    assert notifications(client, task["task_id"])[-1] == \
        {"recipient": EMPLOYEE, "actor": SUPERVISOR, "event": "returned"}


def test_a_returned_step_can_be_resubmitted_by_saving_again(client):
    """The box is still ticked after a return, so the employee's next save --
    a real edit answering the supervisor's note -- asks again."""
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-RETURN-2")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)
    pending = save(client, task, {BOX: "1"})
    login(client, SUPERVISOR)
    returned = transition(client, pending, "return")
    login(client, EMPLOYEE)

    resubmitted = save(client, returned, {BOX: "1"}, comments="addressed the notes")

    assert resubmitted["status"] == "Ready"
    assert len([row for row in notifications(client, task["task_id"])
                if row["event"] == "submitted"]) == 2


def test_an_employee_may_not_approve_the_step(client):
    """The action row hides Approved/Return from an employee; the route refuses
    them regardless of what the client renders."""
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-ROLE-1")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)
    pending = save(client, task, {BOX: "1"})

    transition(client, pending, "approve", expect=403)
    assert slides_task(client, pid)["status"] == "Ready"


# ---------------------------------------------------------------------------
# The field-completion engine keeps its hands off
# ---------------------------------------------------------------------------

def test_a_checked_save_never_approves_the_step(client):
    """The guard, end to end: no save of this step -- ticked, re-ticked, or
    saved by a supervisor -- may reach Approved without a human's click."""
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-ENGINE-1")
    task = slides_task(client, pid)

    saved = save(client, task, {BOX: "1"})
    saved = save(client, saved, {BOX: "1"})
    saved = save(client, saved, {BOX: "1", "seismic_slides_loaded": "1"})

    assert saved["status"] == "Ready"
    events = {row["action_type"] for row in history(client, task["task_id"])}
    assert not events & {"Field Completion", "Field Reopen", "Component Approved"}
    assert tracked_items(client, pid)[STEP] == "Pending Approval"


def test_pending_approval_stays_this_step_alone_under_the_new_flow(client):
    """The board's one display rule, re-pinned for the checkbox path: a save
    that submits Segmentation Slides must not make any OTHER step read
    "Pending Approval"."""
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-BOARD-1")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    # ... and park a sibling in Ready the manual way for contrast.
    other = assign(client, get_task_by_name(client, pid, "Area Definition"), EMPLOYEE)
    login(client, EMPLOYEE)
    save(client, task, {BOX: "1"})
    transition(client, other, "submit")

    items = tracked_items(client, pid)
    assert items[STEP] == "Pending Approval"
    assert [label for label, status in items.items() if status == "Pending Approval"] == [STEP]
    assert items["Area Definition"] == "In Progress"
