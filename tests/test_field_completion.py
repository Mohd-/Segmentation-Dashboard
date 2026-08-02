"""The field-driven completion engine (workflow/constants.py FIELD_COMPLETION +
workflow/lifecycle.py apply_field_completion).

The redesign's detail cards define a step's completion by its FIELD STATE --
the ticked confirmations and the valid inputs -- rather than by a human walking
submit -> approve. Cards 3A (Reservoir CoS) and 3C (Seismic Signature
Validation) are the first two entries; 2B/3B/4A/4B reuse the same engine.

Everything here drives the REAL endpoints (PATCH /api/tasks/<id> with a fields
payload, exactly what the detail form sends) and asserts on what the read
payloads report back, so nothing pins an internal call shape.
"""
from __future__ import annotations

import json

from conftest import create_project, get_task_by_name, raw_sqlite_connect

SUPERVISOR = "Supervisor"
EMPLOYEE = "Employee"

# One valid Reservoir CoS evaluation. The server scores it (cos.calculate_
# reservoir_cos_rows) and stores the row back WITH a reservoir_cos_pct -- that
# stored result is the engine's "valid inputs are present" half of the
# predicate, so the exact numbers here do not matter, only that they score.
RESERVOIR_ROWS = [{"seismic_block": "Block A", "seismic_volume_ar_number": "2525",
                   "amplitude_ratio": "0.5", "base_tight_sarah": "9500", "pull_up": "Yes"}]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def login(client, name):
    resp = client.post("/api/login", json={"name": name})
    assert resp.status_code == 200, resp.get_json()


def save(client, task, fields, expect=200, **extra):
    """PATCH /api/tasks/<id> with a fields payload -- the detail form's save.

    Deliberately sends NO ``status`` key: that is what the v17 UI does, and the
    engine stands down on a save that drives status explicitly.
    """
    body = {"fields": fields, "revision": task["revision"],
            "priority": task.get("priority") or "Medium"}
    body.update(extra)
    resp = client.patch(f"/api/tasks/{task['task_id']}", json=body)
    assert resp.status_code == expect, resp.get_json()
    return resp.get_json()["task"]


def history(client, task_id):
    """Every task_history row for one task, oldest first."""
    conn = raw_sqlite_connect(client.db_path)
    try:
        rows = conn.execute(
            "SELECT action_type, old_status, new_status, changed_by, comment "
            "FROM task_history WHERE task_id = ? ORDER BY history_id", (task_id,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def tracked_item(client, pid, label):
    """One tracked item's board status (the dot the lead card renders)."""
    rows = client.get("/api/projects?pipeline_filter=prospect").get_json()
    row = next(r for r in rows if r["project_id"] == pid)
    return next(item["status"] for item in row["tracked_items"] if item["label"] == label)


def _assign(client, task, assignee):
    resp = client.post(f"/api/tasks/{task['task_id']}/assign",
                       json={"assignee": assignee, "cascade": False, "revision": task["revision"]})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["task"]


def _transition(client, task, action):
    resp = client.post(f"/api/tasks/{task['task_id']}/transition",
                       json={"action": action, "revision": task["revision"]})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["task"]


def drive_to_approved(client, pid, step):
    """Drive a step to Approved the MANUAL way (assign -> submit -> approve).

    Builds the LEGACY shape the grandfather rule protects: a step that is
    Approved while carrying none of the new confirmations.
    """
    task = get_task_by_name(client, pid, step)
    return _transition(client, _transition(client, _assign(client, task, EMPLOYEE), "submit"), "approve")


# ---------------------------------------------------------------------------
# The declarative table itself (pure)
# ---------------------------------------------------------------------------

def test_segmentation_slides_is_never_field_driven():
    """Card 3D keeps the HUMAN approval workflow.

    Segmentation Slides is the one tracked item whose Ready status still reads
    "Pending Approval" on the board -- its submit -> approve walk IS the
    deliverable's review gate. Putting it in FIELD_COMPLETION would silently
    delete a supervisor's job, so the two tables must never overlap.
    """
    import workflow

    assert "Segmentation Slides" not in workflow.FIELD_COMPLETION
    assert workflow.FIELD_COMPLETION_MANUAL_APPROVAL_STEPS == frozenset({"Segmentation Slides"})
    assert not (set(workflow.FIELD_COMPLETION)
                & workflow.FIELD_COMPLETION_MANUAL_APPROVAL_STEPS)


def test_field_completion_only_claims_real_pipeline_steps():
    """Every key is a live step name -- a typo would be a rule that never fires."""
    import workflow

    active = {name for _seq, name, _stage in workflow.PIPELINE_TEMPLATES}
    assert set(workflow.FIELD_COMPLETION) <= active


def test_reopen_is_not_a_public_transition():
    """The engine's Approved -> In Progress move stays off the HTTP surface."""
    import workflow

    assert "reopen" in workflow.ENGINE_TRANSITIONS
    assert "reopen" not in workflow.TASK_TRANSITIONS
    assert workflow.ENGINE_TRANSITIONS["reopen"] == ("Approved", "In Progress")


def test_reopen_is_rejected_by_the_transition_endpoint(client):
    pid = create_project(client, "FC-REOPEN-ROUTE")
    task = drive_to_approved(client, pid, "Seismic Signature Validation")
    resp = client.post(f"/api/tasks/{task['task_id']}/transition",
                       json={"action": "reopen", "revision": task["revision"]})
    assert resp.status_code == 400
    assert "Unknown action" in resp.get_json()["detail"]
    assert get_task_by_name(client, pid, "Seismic Signature Validation")["status"] == "Approved"


# ---------------------------------------------------------------------------
# Card 3C -- Seismic Signature Validation (checkbox only)
# ---------------------------------------------------------------------------

def test_checkbox_save_completes_the_step_with_no_approve_click(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-SEISMIC-1")
    task = get_task_by_name(client, pid, "Seismic Signature Validation")
    assert task["status"] == "Not Assigned"
    assert tracked_item(client, pid, "Seismic Validation") == "In Progress"

    saved = save(client, task, {"seismic_slides_loaded": "1"})

    # The save RESPONSE already carries the post-walk row, so the client adopts
    # the new status/revision instead of sending back a stale pair.
    assert saved["status"] == "Approved"
    assert get_task_by_name(client, pid, "Seismic Signature Validation")["status"] == "Approved"
    assert tracked_item(client, pid, "Seismic Validation") == "Completed"


def test_completion_logs_one_engine_event_naming_the_saving_user(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-SEISMIC-2")
    task = get_task_by_name(client, pid, "Seismic Signature Validation")
    save(client, task, {"seismic_slides_loaded": "1"})

    events = history(client, task["task_id"])
    engine = [row for row in events if row["action_type"] == "Field Completion"]
    assert len(engine) == 1
    assert engine[0]["comment"] == "Completed: required confirmations satisfied"
    assert engine[0]["new_status"] == "Approved"
    # ACTOR STAMPING: the whole walk is audited under the SAVING USER, never
    # 'System' -- the engine is that person's action, just without the clicks.
    assert engine[0]["changed_by"] == SUPERVISOR
    assert {row["changed_by"] for row in events} == {SUPERVISOR}
    # And it really WALKED the machine rather than writing Approved directly.
    assert [row["action_type"] for row in events if row["action_type"].startswith("Component ")] == [
        "Component Inputs Updated", "Component Assigned", "Component Submitted", "Component Approved",
    ]


def test_unticking_the_checkbox_reopens_the_step(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-SEISMIC-3")
    task = get_task_by_name(client, pid, "Seismic Signature Validation")
    approved = save(client, task, {"seismic_slides_loaded": "1"})
    assert approved["status"] == "Approved"

    reopened = save(client, approved, {"seismic_slides_loaded": ""})

    assert reopened["status"] == "In Progress"
    assert reopened["actual_finish"] is None
    assert tracked_item(client, pid, "Seismic Validation") == "In Progress"
    engine = [row for row in history(client, task["task_id"]) if row["action_type"] == "Field Reopen"]
    assert len(engine) == 1
    assert engine[0]["comment"] == "Reopened: required confirmation removed"
    assert (engine[0]["old_status"], engine[0]["new_status"]) == ("Approved", "In Progress")
    assert engine[0]["changed_by"] == SUPERVISOR


def test_reopen_preserves_the_assignee(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-SEISMIC-4")
    task = _assign(client, get_task_by_name(client, pid, "Seismic Signature Validation"), EMPLOYEE)
    approved = save(client, task, {"seismic_slides_loaded": "1"})
    assert approved["assigned_to"] == EMPLOYEE

    reopened = save(client, approved, {"seismic_slides_loaded": ""})
    assert reopened["status"] == "In Progress"
    # The engine closes someone else's step; it never takes it over.
    assert reopened["assigned_to"] == EMPLOYEE


def test_re_saving_a_ticked_checkbox_is_a_no_op(client):
    """The engine is a reconciliation, so replaying the same save adds nothing."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-SEISMIC-5")
    task = get_task_by_name(client, pid, "Seismic Signature Validation")
    approved = save(client, task, {"seismic_slides_loaded": "1"})
    before = len(history(client, task["task_id"]))

    again = save(client, approved, {"seismic_slides_loaded": "1"})
    assert again["status"] == "Approved"
    assert len(history(client, task["task_id"])) == before


def test_seismic_signature_validation_never_reads_pending_approval(client):
    """Only Segmentation Slides maps Ready -> "Pending Approval"; this step
    passes through Ready inside the engine's walk and must never surface it."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-SEISMIC-6")
    task = get_task_by_name(client, pid, "Seismic Signature Validation")
    # Even parked in Ready by the MANUAL path, the board reads In Progress.
    _transition(client, _assign(client, task, EMPLOYEE), "submit")
    assert tracked_item(client, pid, "Seismic Validation") == "In Progress"

    fresh = get_task_by_name(client, pid, "Seismic Signature Validation")
    save(client, fresh, {"seismic_slides_loaded": "1"})
    assert tracked_item(client, pid, "Seismic Validation") == "Completed"


# ---------------------------------------------------------------------------
# Card 3A -- Reservoir CoS (checkbox AND a stored, scored result)
# ---------------------------------------------------------------------------

def test_reservoir_checkbox_alone_is_not_completion(client):
    """The predicate needs BOTH halves: an empty mini-sheet has no CoS to carry
    into the lead's Total Chance of Success, so the box alone cannot close it."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-RESERVOIR-1")
    task = get_task_by_name(client, pid, "Reservoir CoS")

    saved = save(client, task, {"reservoir_slides_loaded": "1"})
    assert saved["status"] == "Not Assigned"
    assert tracked_item(client, pid, "Reservoir") == "In Progress"

    # An explicitly EMPTY sheet is stored as the non-blank string "[]" -- still
    # no result, still not complete.
    saved = save(client, saved, {"reservoir_slides_loaded": "1", "reservoir_cos_rows": json.dumps([])})
    assert saved["status"] == "Not Assigned"


def test_reservoir_rows_alone_are_not_completion(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-RESERVOIR-2")
    task = get_task_by_name(client, pid, "Reservoir CoS")

    saved = save(client, task, {"reservoir_cos_rows": json.dumps(RESERVOIR_ROWS)})
    stored = client.get(f"/api/tasks/{task['task_id']}/dynamic-fields").get_json()
    assert json.loads(stored["reservoir_cos_rows"])[0]["reservoir_cos_pct"]  # scored
    assert saved["status"] == "Not Assigned"
    assert tracked_item(client, pid, "Reservoir") == "In Progress"


def test_reservoir_rows_plus_checkbox_completes(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-RESERVOIR-3")
    task = get_task_by_name(client, pid, "Reservoir CoS")

    saved = save(client, task, {"reservoir_cos_rows": json.dumps(RESERVOIR_ROWS),
                                "reservoir_slides_loaded": "1"})
    assert saved["status"] == "Approved"
    assert tracked_item(client, pid, "Reservoir") == "Completed"

    # And unticking on a later save reopens it, rows untouched.
    reopened = save(client, saved, {"reservoir_slides_loaded": ""})
    assert reopened["status"] == "In Progress"
    stored = client.get(f"/api/tasks/{task['task_id']}/dynamic-fields").get_json()
    assert json.loads(stored["reservoir_cos_rows"])[0]["reservoir_cos_pct"]


def test_clearing_the_rows_reopens_a_completed_reservoir_step(client):
    """The other half of the predicate is just as live as the checkbox."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-RESERVOIR-4")
    task = get_task_by_name(client, pid, "Reservoir CoS")
    approved = save(client, task, {"reservoir_cos_rows": json.dumps(RESERVOIR_ROWS),
                                   "reservoir_slides_loaded": "1"})
    assert approved["status"] == "Approved"

    reopened = save(client, approved, {"reservoir_cos_rows": json.dumps([])})
    assert reopened["status"] == "In Progress"


# ---------------------------------------------------------------------------
# The grandfather rule
# ---------------------------------------------------------------------------

def test_legacy_approved_step_survives_saves_of_other_tasks(client):
    """THE GRANDFATHER RULE.

    A step approved before these checkboxes existed carries none of them, so
    the predicate reads "not met". It must NEVER be reopened by anything other
    than the user's own save OF THAT STEP -- not by reading the project, not by
    saving a sibling, not by any sweep (there is no sweep).
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-GRANDFATHER-1")
    legacy = drive_to_approved(client, pid, "Seismic Signature Validation")
    assert legacy["status"] == "Approved"
    assert client.get(f"/api/tasks/{legacy['task_id']}/dynamic-fields").get_json() == {}

    # Save a DIFFERENT step -- including the other field-driven one.
    other = get_task_by_name(client, pid, "Thickness Estimation")
    save(client, other, {"formation_thickness_ft": "120"})
    reservoir = get_task_by_name(client, pid, "Reservoir CoS")
    save(client, reservoir, {"reservoir_cos_rows": json.dumps(RESERVOIR_ROWS),
                             "reservoir_slides_loaded": "1"})
    # ... and read the project every way the board and detail views do.
    client.get(f"/api/projects/{pid}").get_json()
    client.get(f"/api/projects/{pid}/detail").get_json()
    client.get("/api/projects?pipeline_filter=prospect").get_json()

    assert get_task_by_name(client, pid, "Seismic Signature Validation")["status"] == "Approved"
    assert tracked_item(client, pid, "Seismic Validation") == "Completed"
    assert not [row for row in history(client, legacy["task_id"])
                if row["action_type"] == "Field Reopen"]


def test_legacy_approved_step_reopens_only_once_its_own_form_is_saved(client):
    """The flip side: the rule is not "never", it is "not until you edit it".

    Opening the legacy step and saving it IS the user choosing what the field
    state should be, and the engine then reconciles to what is on screen.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-GRANDFATHER-2")
    legacy = drive_to_approved(client, pid, "Seismic Signature Validation")

    touched = save(client, legacy, {"seismic_slides_loaded": ""})
    assert touched["status"] == "In Progress"


def test_a_save_that_drives_status_explicitly_stands_the_engine_down(client):
    """PATCH with a ``status`` key is a caller driving status directly (the
    legacy path; the v17 UI never sends it). The engine must not reconcile a
    deliberate choice straight back out."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-EXPLICIT-1")
    task = get_task_by_name(client, pid, "Seismic Signature Validation")

    saved = save(client, task, {}, status="Approved")
    assert saved["status"] == "Approved"
    assert not [row for row in history(client, task["task_id"])
                if row["action_type"] in ("Field Completion", "Field Reopen")]


def test_steps_outside_the_table_are_untouched_by_their_own_saves(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-SCOPE-1")
    slides = drive_to_approved(client, pid, "Segmentation Slides")
    assert slides["status"] == "Approved"

    # Segmentation Slides has no FIELD_COMPLETION entry, so saving it (with or
    # without any confirmation-shaped key) never moves it.
    touched = save(client, slides, {"seismic_slides_loaded": ""})
    assert touched["status"] == "Approved"
    assert tracked_item(client, pid, "Segmentation Slides") == "Completed"


# ---------------------------------------------------------------------------
# Notification suppression
# ---------------------------------------------------------------------------

def test_the_engine_walk_does_not_spam_supervisors_with_a_submit(client):
    """The walk's submit is followed by its own approve microseconds later, so
    telling every supervisor "X submitted Y" would be a request for an approval
    that was never theirs to grant (the SYSTEM_USER rule, generalized)."""
    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute("INSERT INTO users (name, role, is_active, created_at) "
                     "VALUES ('Second Supervisor', 'supervisor', 1, datetime('now'))")
        conn.commit()
    finally:
        conn.close()

    login(client, SUPERVISOR)
    pid = create_project(client, "FC-NOTIFY-1")
    task = _assign(client, get_task_by_name(client, pid, "Seismic Signature Validation"), EMPLOYEE)
    login(client, EMPLOYEE)   # the assignee ticks their own box and saves
    save(client, task, {"seismic_slides_loaded": "1"})

    conn = raw_sqlite_connect(client.db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT recipient, event FROM notifications WHERE task_id = ?", (task["task_id"],))]
    finally:
        conn.close()
    assert [r for r in rows if r["event"] == "submitted"] == []
    # The approve resolves to "recipient == actor" (the saver owns the step), so
    # it is suppressed by the pre-existing generic rule too.
    assert rows == []


def test_a_manual_submit_still_notifies_supervisors(client):
    """The suppression is scoped to engine-driven walks -- the human path is
    untouched."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-NOTIFY-2")
    task = _assign(client, get_task_by_name(client, pid, "Segmentation Slides"), EMPLOYEE)
    login(client, EMPLOYEE)
    _transition(client, task, "submit")

    conn = raw_sqlite_connect(client.db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT recipient, event FROM notifications WHERE task_id = ?", (task["task_id"],))]
    finally:
        conn.close()
    assert rows == [{"recipient": SUPERVISOR, "event": "submitted"}]
