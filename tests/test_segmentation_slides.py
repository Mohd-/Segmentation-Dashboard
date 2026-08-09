"""Cards 3D and 3S -- the Segmentation Slides approval workflow.

The one tracked item whose completion is a HUMAN decision.

Card 3S moved it onto the Business Plan Execution approval framework, which
changed HOW the request for review is made. It used to be implicit: ticking
"Segmentation slides placed in the shared folder" and saving submitted the
step, because the page offered no Submit button. Under the shared framework a
save is NEVER a submission -- the employee submits explicitly, the box is a
REQUIREMENT that submit checks (REQUIRED_FIELDS_FOR_SUBMIT), and a supervisor
may additionally reopen an approved step. Only a supervisor's Approved click
still closes it.

Everything here drives the REAL endpoints (PATCH /api/tasks/<id> with a fields
payload -- exactly what the detail form's Save sends -- and POST
/api/tasks/<id>/transition), so nothing pins an internal call shape.
"""
from __future__ import annotations

from conftest import create_project, get_task_by_name, raw_sqlite_connect, reach_task

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


def submit(client, task, expect=200):
    """The explicit request for review Card 3S introduced."""
    return transition(client, task, "submit", expect=expect)


def transition(client, task, action, expect=200):
    resp = client.post(f"/api/tasks/{task['task_id']}/transition",
                       json={"action": action, "revision": task["revision"]})
    assert resp.status_code == expect, resp.get_json()
    return resp.get_json().get("task")


def assign(client, task, assignee):
    if task.get("status") == "Not Assigned":
        task = reach_task(client, task["project_id"], task["task_name"])
    resp = client.post(f"/api/tasks/{task['task_id']}/assign",
                       json={"assigned_to": assignee, "cascade": False, "revision": task["revision"]})
    assert resp.status_code == 200, resp.get_json()
    # These tests assert the notification fan-out of the submit/approve walk;
    # strip out the assignment notification so the assertions stay focused.
    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute("DELETE FROM notifications WHERE task_id = ? AND event = 'assigned'",
                     (task["task_id"],))
        conn.commit()
    finally:
        conn.close()
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

def test_no_step_submits_itself_on_save_any_more():
    """Card 3S emptied CHECKBOX_SUBMIT_STEPS.

    Segmentation Slides was its only entry. Under the shared approval framework
    a save is never a submission, so the table is empty -- but the mechanism is
    kept, because "a save IS the request for review" is a policy this codebase
    may want again, and deleting it would throw away the capability rather than
    the decision.
    """
    import workflow

    assert workflow.CHECKBOX_SUBMIT_STEPS == {}
    assert not (set(workflow.CHECKBOX_SUBMIT_STEPS) & set(workflow.FIELD_COMPLETION))


def test_the_confirmation_is_now_a_submit_REQUIREMENT(client):
    """The box did not disappear -- it became what it reads as. Submitting
    without it is refused, naming the box."""
    import workflow

    assert workflow.REQUIRED_FIELDS_FOR_SUBMIT[STEP] == (
        (BOX, "Segmentation slides placed in the shared folder"),)

    login(client, SUPERVISOR)
    pid = create_project(client, "SS-REQUIRE-1")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)

    resp = client.post(f"/api/tasks/{task['task_id']}/transition",
                       json={"action": "submit", "revision": task["revision"]})
    assert resp.status_code == 400
    assert "shared folder" in resp.get_json()["detail"]
    # Refused BEFORE any state change: no pending review was created.
    assert slides_task(client, pid)["status"] == "In Progress"


def test_the_submission_only_ever_moves_a_step_to_ready():
    import workflow

    assert workflow.TASK_TRANSITIONS["submit"] == ("In Progress", "Ready")


# ---------------------------------------------------------------------------
# The employee's save = submit
# ---------------------------------------------------------------------------

def test_an_explicit_submit_files_the_request_for_review(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-SUBMIT-1")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    assert task["status"] == "In Progress"
    login(client, EMPLOYEE)

    # A save is a DRAFT, whatever it carries.
    saved = save(client, task, {BOX: "1"})
    assert saved["status"] == "In Progress", "a save is never a submission"

    saved = submit(client, saved)
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

    submit(client, save(client, task, {BOX: "1"}))

    assert notifications(client, task["task_id"]) == \
        [{"recipient": SUPERVISOR, "actor": EMPLOYEE, "event": "submitted"}]


def test_saving_a_pending_step_is_locked_and_does_not_submit_again(client):
    """A step already Ready is immutable until a supervisor returns it."""
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-SUBMIT-2")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)
    pending = submit(client, save(client, task, {BOX: "1"}))

    save(client, pending, {BOX: "1"}, comments="second thoughts", expect=400)

    assert slides_task(client, pid)["status"] == "Ready"
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


def test_unticking_cannot_withdraw_a_locked_pending_submission(client):
    """There is no "withdraw" in the lifecycle, and inventing one here would
    silently cancel a review the supervisor may already be reading."""
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-DRAFT-2")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)
    pending = submit(client, save(client, task, {BOX: "1"}))
    assert pending["status"] == "Ready"

    save(client, pending, {BOX: ""}, expect=400)

    assert slides_task(client, pid)["status"] == "Ready"
    assert tracked_items(client, pid)[STEP] == "Pending Approval"


def test_submitting_an_unassigned_step_is_refused_for_an_employee(client):
    """Under the shared framework an employee may only submit work assigned to
    them -- the same rule every other step follows. Ticking a box no longer
    quietly assigns the step to whoever ticked it.

    Created ANONYMOUSLY (before login) so the step really is Not Assigned.
    """
    pid = create_project(client, "SS-ASSIGN-1")
    login(client, EMPLOYEE)
    task = slides_task(client, pid)
    assert task["status"] == "Not Assigned"

    save(client, task, {BOX: "1"}, expect=403)
    assert slides_task(client, pid)["status"] == "Not Assigned"
    submit(client, task, expect=403)


def test_a_supervisor_may_submit_a_step_assigned_to_someone_else(client):
    """Supervisors and staff may submit any component; the submit does not take
    the step over."""
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-ASSIGN-2")
    task = assign(client, slides_task(client, pid), EMPLOYEE)

    saved = submit(client, save(client, task, {BOX: "1"}))

    assert (saved["status"], saved["assigned_to"]) == ("Ready", EMPLOYEE)


# ---------------------------------------------------------------------------
# The supervisor's decision
# ---------------------------------------------------------------------------

def test_supervisor_approval_completes_the_tracked_item(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-APPROVE-1")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)
    pending = submit(client, save(client, task, {BOX: "1"}))
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
    pending = submit(client, save(client, task, {BOX: "1"}))
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
    pending = submit(client, save(client, task, {BOX: "1"}, comments="ready for review"))
    login(client, SUPERVISOR)

    returned = transition(client, pending, "return")

    assert returned["status"] == "In Progress"
    assert returned["assigned_to"] == EMPLOYEE
    assert returned["comments"] == "ready for review"
    assert client.get(f"/api/tasks/{task['task_id']}/dynamic-fields").get_json()[BOX] == "1"
    assert tracked_items(client, pid)[STEP] == "In Progress"
    assert notifications(client, task["task_id"])[-1] == \
        {"recipient": EMPLOYEE, "actor": SUPERVISOR, "event": "returned"}


def test_a_returned_step_can_be_resubmitted(client):
    """A return preserves everything, so answering the note and submitting
    again is the whole correction flow."""
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-RETURN-2")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)
    pending = submit(client, save(client, task, {BOX: "1"}))
    login(client, SUPERVISOR)
    returned = transition(client, pending, "return")
    login(client, EMPLOYEE)

    edited = save(client, returned, {BOX: "1"}, comments="addressed the notes")
    resubmitted = submit(client, edited)

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
    pending = submit(client, save(client, task, {BOX: "1"}))

    transition(client, pending, "approve", expect=403)
    assert slides_task(client, pid)["status"] == "Ready"


# ---------------------------------------------------------------------------
# The field-completion engine keeps its hands off
# ---------------------------------------------------------------------------

def test_no_save_of_this_step_ever_approves_it(client):
    """The guard, end to end: no save -- ticked, re-ticked, or made by a
    supervisor -- may reach Approved, or even Ready, without a click.

    The step is deliberately absent from FIELD_COMPLETION, so the engine that
    closes every other prospect step on its field state cannot touch it.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-ENGINE-1")
    task = slides_task(client, pid)

    saved = save(client, task, {BOX: "1"})
    saved = save(client, saved, {BOX: "1"})
    saved = save(client, saved, {BOX: "1", "seismic_slides_loaded": "1"})

    assert saved["status"] != "Approved"
    assert saved["status"] != "Ready", "a save is never a submission"
    events = {row["action_type"] for row in history(client, task["task_id"])}
    assert not events & {"Field Completion", "Field Reopen", "Component Approved",
                         "Component Submitted"}


def test_pending_approval_stays_this_step_alone_under_the_new_flow(client):
    """The board's one display rule, re-pinned for the checkbox path: a save
    that submits Segmentation Slides must not make any OTHER step read
    "Pending Approval"."""
    import db as dbmod
    import workflow

    login(client, SUPERVISOR)
    pid = create_project(client, "SS-BOARD-1")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    # ... and park a sibling in Ready via the domain save_task for contrast.
    other = get_task_by_name(client, pid, "Lead Assessment")
    session = dbmod.new_session()
    try:
        workflow.save_task(session, other["task_id"], {"status": "Ready"})
    finally:
        session.close()
    other = client.get(f"/api/tasks/{other['task_id']}").get_json()
    assert other["status"] == "Ready"
    login(client, EMPLOYEE)
    submit(client, save(client, task, {BOX: "1"}))

    items = tracked_items(client, pid)
    assert items[STEP] == "Pending Approval"
    assert [label for label, status in items.items() if status == "Pending Approval"] == [STEP]
    assert items["Area Definition"] == "In Progress"


# ---------------------------------------------------------------------------
# Card 3S -- reopening an approved step
# ---------------------------------------------------------------------------

def test_a_supervisor_may_reopen_an_approved_step(client):
    """The framework's last transition. The earlier approval is NOT undone in
    the record: task_history is append-only, so reopening adds an event."""
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-REOPEN-1")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)
    pending = submit(client, save(client, task, {BOX: "1"}))
    login(client, SUPERVISOR)
    approved = transition(client, pending, "approve")
    assert tracked_items(client, pid)[STEP] == "Completed"

    reopened = transition(client, approved, "reopen")

    assert reopened["status"] == "In Progress"
    assert reopened["assigned_to"] == EMPLOYEE, "the assignee is preserved"
    assert tracked_items(client, pid)[STEP] == "In Progress"
    # Both events are in the trail, in order -- the approval was not erased.
    events = [row["action_type"] for row in history(client, task["task_id"])]
    assert events.index("Component Approved") < events.index("Component Reopened")
    # And the confirmation is still stored, so the correction starts from the
    # work that was already done.
    assert client.get(f"/api/tasks/{task['task_id']}/dynamic-fields").get_json()[BOX] == "1"


def test_an_employee_may_not_reopen_an_approved_step(client):
    """Reopening used to be engine-only precisely because publishing it
    ungated would be an un-approve anyone could reach. It is published now,
    supervisor-gated at the route."""
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-REOPEN-2")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)
    pending = submit(client, save(client, task, {BOX: "1"}))
    login(client, SUPERVISOR)
    approved = transition(client, pending, "approve")

    login(client, EMPLOYEE)
    transition(client, approved, "reopen", expect=403)
    assert slides_task(client, pid)["status"] == "Approved"


def test_a_refused_submission_leaves_the_step_usable(client):
    """Card 3S is explicit that a failed submission must not reproduce the BPE
    navigation lock. Server-side that means: no state change, no pending row,
    and the very next legitimate submit still works."""
    login(client, SUPERVISOR)
    pid = create_project(client, "SS-REFUSE-1")
    task = assign(client, slides_task(client, pid), EMPLOYEE)
    login(client, EMPLOYEE)

    submit(client, task, expect=400)
    assert slides_task(client, pid)["status"] == "In Progress"
    assert not [row for row in history(client, task["task_id"])
                if row["action_type"] == "Component Submitted"]

    fixed = save(client, slides_task(client, pid), {BOX: "1"})
    assert submit(client, fixed)["status"] == "Ready"
