"""Tests for the v17 status-model rework (WS4).

The 9-status dropdown is replaced by an implicit 4-state lifecycle:
Not Assigned -> In Progress (assignment) -> Ready (submit) -> Approved
(supervisor), with Return sending Ready back to In Progress. There is no stored
"not applicable" state; that name is rejected at the API like any other
non-lifecycle status.

Covers: POST /assign target + cascade semantics, POST /transition happy paths
and wrong-state 400s, role gates (employee vs staff/supervisor), save_task's
optional status / preserved assignee, and API rejection of legacy statuses.
"""
from __future__ import annotations

from conftest import create_project, get_tasks

PROSPECT_STAGES = {"Lead Assessment", "Risk Analysis", "Pre-Well Delivery"}


def _assign(client, task_id, assignee, revision, cascade=False):
    resp = client.post(f"/api/tasks/{task_id}/assign", json={
        "assignee": assignee, "cascade": cascade, "revision": revision,
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["task"]


def _transition(client, task_id, action, revision):
    return client.post(f"/api/tasks/{task_id}/transition", json={
        "action": action, "revision": revision,
    })


def _login(client, name):
    resp = client.post("/api/login", json={"name": name})
    assert resp.status_code == 200, resp.get_json()


# ---------------------------------------------------------------------------
# Lifecycle happy paths (anonymous dev mode acts as supervisor)
# ---------------------------------------------------------------------------

def test_assign_moves_not_assigned_to_in_progress_and_stamps_start(client):
    pid = create_project(client, "LIFECYCLE-ASSIGN-1")
    task = get_tasks(client, pid)[0]
    assert task["status"] == "Not Assigned"

    after = _assign(client, task["task_id"], "Employee", task["revision"])
    assert after["status"] == "In Progress"
    assert after["assigned_to"] == "Employee"
    assert after["actual_start"] is not None

    # The project's current owner follows the assignment.
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["current_owner"] == "Employee"


def test_reassigning_an_in_progress_task_keeps_its_status(client):
    pid = create_project(client, "LIFECYCLE-REASSIGN-1")
    task = get_tasks(client, pid)[0]
    first = _assign(client, task["task_id"], "Employee", task["revision"])
    second = _assign(client, task["task_id"], "Staff Member", first["revision"])
    assert second["status"] == "In Progress"  # reassignment, not a state change
    assert second["assigned_to"] == "Staff Member"


def test_submit_approve_flow_advances_project(client):
    pid = create_project(client, "LIFECYCLE-FLOW-1")
    task = get_tasks(client, pid)[0]
    assigned = _assign(client, task["task_id"], "Employee", task["revision"])

    resp = _transition(client, task["task_id"], "submit", assigned["revision"])
    assert resp.status_code == 200
    ready = resp.get_json()["task"]
    assert ready["status"] == "Ready"
    assert ready["actual_finish"] is None

    resp = _transition(client, task["task_id"], "approve", ready["revision"])
    assert resp.status_code == 200
    approved = resp.get_json()["task"]
    assert approved["status"] == "Approved"
    assert approved["actual_finish"] is not None
    assert approved["actual_start"] is not None

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["current_task"] == "Thickness Estimation"


def test_return_sends_ready_back_to_in_progress(client):
    pid = create_project(client, "LIFECYCLE-RETURN-1")
    task = get_tasks(client, pid)[0]
    assigned = _assign(client, task["task_id"], "Employee", task["revision"])
    ready = _transition(client, task["task_id"], "submit", assigned["revision"]).get_json()["task"]

    resp = _transition(client, task["task_id"], "return", ready["revision"])
    assert resp.status_code == 200
    returned = resp.get_json()["task"]
    assert returned["status"] == "In Progress"
    assert returned["actual_finish"] is None
    assert returned["assigned_to"] == "Employee"  # return keeps the assignee


# ---------------------------------------------------------------------------
# Wrong-state and unknown-action validation
# ---------------------------------------------------------------------------

def test_submit_requires_in_progress(client):
    pid = create_project(client, "WRONGSTATE-1")
    task = get_tasks(client, pid)[0]  # Not Assigned
    resp = _transition(client, task["task_id"], "submit", task["revision"])
    assert resp.status_code == 400
    assert "Not Assigned" in resp.get_json()["detail"]


def test_approve_and_return_require_ready(client):
    pid = create_project(client, "WRONGSTATE-2")
    task = get_tasks(client, pid)[0]
    assigned = _assign(client, task["task_id"], "Employee", task["revision"])  # In Progress

    resp = _transition(client, task["task_id"], "approve", assigned["revision"])
    assert resp.status_code == 400
    resp = _transition(client, task["task_id"], "return", assigned["revision"])
    assert resp.status_code == 400


def test_unknown_transition_action_rejected(client):
    pid = create_project(client, "WRONGSTATE-3")
    task = get_tasks(client, pid)[0]
    resp = _transition(client, task["task_id"], "escalate", task["revision"])
    assert resp.status_code == 400
    assert "Unknown action" in resp.get_json()["detail"]


# ---------------------------------------------------------------------------
# Role gates
# ---------------------------------------------------------------------------

def test_employee_cannot_assign(client):
    pid = create_project(client, "ROLES-ASSIGN-1")
    task = get_tasks(client, pid)[0]
    _login(client, "Employee")
    resp = client.post(f"/api/tasks/{task['task_id']}/assign", json={
        "assignee": "Employee", "revision": task["revision"],
    })
    assert resp.status_code == 403


def test_only_supervisor_can_approve_and_only_assignee_or_supervisor_can_return(client):
    pid = create_project(client, "ROLES-APPROVE-1")
    task = get_tasks(client, pid)[0]
    _login(client, "Supervisor")
    assigned = _assign(client, task["task_id"], "Employee", task["revision"])
    ready = _transition(client, task["task_id"], "submit", assigned["revision"]).get_json()["task"]

    _login(client, "Employee")
    resp = _transition(client, task["task_id"], "approve", ready["revision"])
    assert resp.status_code == 403
    resp = _transition(client, task["task_id"], "return", ready["revision"])
    assert resp.status_code == 200
    returned = resp.get_json()["task"]
    assert returned["status"] == "In Progress"

    # Staff may not approve either -- supervisor only.
    _login(client, "Staff Member")
    resp = _transition(client, task["task_id"], "approve", returned["revision"])
    assert resp.status_code == 403

    # Staff can return a different Ready component just like an employee.
    _login(client, "Supervisor")
    staff_task = get_tasks(client, pid)[1]
    staff_assigned = _assign(client, staff_task["task_id"], "Staff Member", staff_task["revision"])
    staff_ready = _transition(client, staff_task["task_id"], "submit", staff_assigned["revision"]).get_json()["task"]
    _login(client, "Staff Member")
    resp = _transition(client, staff_task["task_id"], "return", staff_ready["revision"])
    assert resp.status_code == 200
    assert resp.get_json()["task"]["status"] == "In Progress"

    _login(client, "Supervisor")
    ready_again = _transition(client, task["task_id"], "submit", returned["revision"]).get_json()["task"]
    _login(client, "Staff Member")
    resp = _transition(client, task["task_id"], "return", ready_again["revision"])
    assert resp.status_code == 403
    assert "assigned to you" in resp.get_json()["detail"]

    _login(client, "Supervisor")
    resp = _transition(client, task["task_id"], "approve", ready_again["revision"])
    assert resp.status_code == 200


def test_only_supervisor_can_set_priority(client):
    pid = create_project(client, "ROLES-PRIORITY-1")
    task = get_tasks(client, pid)[0]

    for name in ("Employee", "Staff Member"):
        _login(client, name)
        resp = client.patch(f"/api/tasks/{task['task_id']}/priority", json={"priority": "High"})
        assert resp.status_code == 403

    _login(client, "Supervisor")
    resp = client.patch(f"/api/tasks/{task['task_id']}/priority", json={"priority": "High"})
    assert resp.status_code == 200
    assert get_tasks(client, pid)[0]["priority"] == "High"


def test_save_cannot_bypass_the_priority_gate(client):
    """A non-supervisor's Save keeps the stored priority, whatever it sends."""
    pid = create_project(client, "ROLES-PRIORITY-2")
    task = get_tasks(client, pid)[0]
    _login(client, "Supervisor")
    assert client.patch(f"/api/tasks/{task['task_id']}/priority", json={"priority": "High"}).status_code == 200
    task = get_tasks(client, pid)[0]

    _login(client, "Employee")
    resp = client.patch(f"/api/tasks/{task['task_id']}", json={
        "priority": "Low", "comments": "note", "revision": task["revision"],
    })
    assert resp.status_code == 200
    assert resp.get_json()["task"]["priority"] == "High"
    # The rest of the save still lands.
    assert resp.get_json()["task"]["comments"] == "note"

    _login(client, "Supervisor")
    task = get_tasks(client, pid)[0]
    resp = client.patch(f"/api/tasks/{task['task_id']}", json={
        "priority": "Low", "comments": "note", "revision": task["revision"],
    })
    assert resp.get_json()["task"]["priority"] == "Low"


def test_employee_can_submit_own_task_but_not_someone_elses(client):
    pid = create_project(client, "ROLES-SUBMIT-1")
    tasks = get_tasks(client, pid)
    mine, theirs = tasks[0], tasks[1]

    _login(client, "Supervisor")
    mine_assigned = _assign(client, mine["task_id"], "Employee", mine["revision"])
    theirs_assigned = _assign(client, theirs["task_id"], "Staff Member", theirs["revision"])

    _login(client, "Employee")
    resp = _transition(client, theirs["task_id"], "submit", theirs_assigned["revision"])
    assert resp.status_code == 403
    assert "assigned to you" in resp.get_json()["detail"]

    resp = _transition(client, mine["task_id"], "submit", mine_assigned["revision"])
    assert resp.status_code == 200
    assert resp.get_json()["task"]["status"] == "Ready"


def test_staff_can_submit_a_task_assigned_to_someone_else(client):
    pid = create_project(client, "ROLES-SUBMIT-2")
    task = get_tasks(client, pid)[0]
    _login(client, "Supervisor")
    assigned = _assign(client, task["task_id"], "Employee", task["revision"])
    _login(client, "Staff Member")
    resp = _transition(client, task["task_id"], "submit", assigned["revision"])
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Cascade semantics
# ---------------------------------------------------------------------------

def test_assign_cascade_covers_subsequent_not_assigned_prospect_steps_only(client):
    pid = create_project(client, "CASCADE-1")
    tasks = get_tasks(client, pid)
    by_seq = {t["sequence_no"]: t for t in tasks}

    # Pre-existing work that the cascade must never touch:
    # step 5 Approved, step 6 In Progress and assigned to Employee.
    resp = client.patch(f"/api/tasks/{by_seq[5]['task_id']}", json={
        "status": "Approved", "revision": by_seq[5]["revision"],
    })
    assert resp.status_code == 200
    _assign(client, by_seq[6]["task_id"], "Employee", by_seq[6]["revision"])

    # Assign step 3 with cascade: steps 3..12 (the prospect pipeline since the
    # v18 renumbering) that are still Not Assigned all go In Progress with the
    # same assignee.
    _assign(client, by_seq[3]["task_id"], "Staff Member", by_seq[3]["revision"], cascade=True)

    after = {t["sequence_no"]: t for t in get_tasks(client, pid)}
    # Steps before the target are untouched.
    for seq in (1, 2):
        assert after[seq]["status"] == "Not Assigned"
        assert after[seq]["assigned_to"] is None
    # Target + subsequent Not Assigned prospect steps cascade.
    for seq in (3, 4, 7, 8, 9, 10, 11, 12):
        assert after[seq]["status"] == "In Progress", seq
        assert after[seq]["assigned_to"] == "Staff Member", seq
        assert after[seq]["revision"] == by_seq[seq]["revision"] + 1, seq
    # Pre-existing Approved / In Progress rows are never touched.
    assert after[5]["status"] == "Approved"
    assert after[5]["assigned_to"] is None
    assert after[6]["status"] == "In Progress"
    assert after[6]["assigned_to"] == "Employee"
    # BP-stage tasks are outside a prospect's applicable pipeline.
    for seq, task in after.items():
        if task["stage_group"] not in PROSPECT_STAGES:
            assert task["status"] == "Not Assigned", seq
            assert task["assigned_to"] is None, seq


def test_assign_without_cascade_touches_only_the_target(client):
    pid = create_project(client, "CASCADE-2")
    tasks = get_tasks(client, pid)
    _assign(client, tasks[2]["task_id"], "Employee", tasks[2]["revision"], cascade=False)
    after = get_tasks(client, pid)
    changed = [t for t in after if t["assigned_to"]]
    assert [t["task_id"] for t in changed] == [tasks[2]["task_id"]]


def test_assign_cascade_for_bp_project_stays_in_bp_stages(client):
    pid = create_project(
        client, "CASCADE-BP-1", pipeline_type="bp",
        business_plan_enabled=True, business_plan_year=2028,
    )
    tasks = get_tasks(client, pid)
    # All 31 rows seed Not Assigned; the BP pipeline scopes the cascade to
    # BP-stage tasks. Anchor on the first BP-stage task explicitly.
    first_bp = next(t for t in tasks if t["stage_group"] not in PROSPECT_STAGES)
    _assign(client, first_bp["task_id"], "Employee", first_bp["revision"], cascade=True)
    after = get_tasks(client, pid)
    for task in after:
        if task["stage_group"] in PROSPECT_STAGES:
            # Outside the BP well's operating pipeline: untouched by the cascade.
            assert task["status"] == "Not Assigned", task["task_name"]
            assert task["assigned_to"] is None, task["task_name"]
        else:
            assert task["status"] == "In Progress", task["task_name"]
            assert task["assigned_to"] == "Employee"


# ---------------------------------------------------------------------------
# save_task: optional status, preserved assignee, rejected statuses
# ---------------------------------------------------------------------------

def test_save_without_status_keeps_current_status_and_assignee(client):
    pid = create_project(client, "SAVE-KEEP-1")
    task = get_tasks(client, pid)[0]
    assigned = _assign(client, task["task_id"], "Employee", task["revision"])

    resp = client.patch(f"/api/tasks/{task['task_id']}", json={
        "fields": {"p90_area_km2": "12"}, "revision": assigned["revision"],
    })
    assert resp.status_code == 200
    saved = resp.get_json()["task"]
    assert saved["status"] == "In Progress"       # NOT reset to Not Assigned
    assert saved["assigned_to"] == "Employee"     # NOT cleared


def test_save_rejects_not_applicable_and_legacy_statuses(client):
    pid = create_project(client, "SAVE-REJECT-1")
    task = get_tasks(client, pid)[0]
    for bad in ("Not Applicable", "Under Review", "Ready for Review",
                "Ready for Approval", "Returned for Update", "Assigned"):
        resp = client.patch(f"/api/tasks/{task['task_id']}", json={
            "status": bad, "revision": task["revision"],
        })
        assert resp.status_code == 400, bad
        assert resp.get_json()["detail"] == "Invalid component status."
