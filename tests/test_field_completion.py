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
# Card 3B -- Trap and Seal CoS (checkbox AND BOTH stored CoS results)
# ---------------------------------------------------------------------------

# The Seal form's five inputs, in the shape cos.calculate_seal_cos scores:
# activity > 0.9 takes the activity x fracture-permeability branch -> "48".
SEAL_INPUTS = {
    "seal_recent_activity_age": "0.95",
    "seal_dip": "0.3",
    "seal_azimuth_vs_shmax": "0.6",
    "seal_fault_level_confidence": "0.9",
    "seal_fracture_permeability": "0.5",
}
COS_STEP = "Trap and Seal CoS"


def _thickness(client, pid, value="100"):
    """Save the cross-task input the Trap recompute reads (Sarah prognosis)."""
    save(client, get_task_by_name(client, pid, "Thickness Estimation"),
         {"formation_thickness_ft": value})


def _stored(client, task_id):
    return client.get(f"/api/tasks/{task_id}/dynamic-fields").get_json()


def test_trap_and_seal_checkbox_alone_is_not_completion(client):
    """The confirmation is one third of the predicate: with neither half of the
    merged step scored there is no CoS to carry into the Total."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-TRAPSEAL-1")
    task = get_task_by_name(client, pid, COS_STEP)

    saved = save(client, task, {"seal_slides_loaded": "1"})
    assert saved["status"] == "Not Assigned"
    assert tracked_item(client, pid, "Trap and Seal") == "In Progress"


def test_trap_complete_with_seal_incomplete_stays_in_progress(client):
    """THE MERGE'S POINT: half a merged step is not a completed step.

    The Trap half scores (the server's recompute writes trap_cos_pct) and the
    confirmation is ticked -- but the Seal form is untouched, so seal_cos_pct
    was never computed and the step must stay open.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-TRAPSEAL-2")
    _thickness(client, pid)
    task = get_task_by_name(client, pid, COS_STEP)

    saved = save(client, task, {"sarah_quwarah_thickness_ft": "130",
                                "seal_slides_loaded": "1"})
    stored = _stored(client, task["task_id"])
    assert stored["trap_cos_pct"] == "80"          # the Trap half really scored
    assert not stored.get("seal_cos_pct")          # ... and the Seal half did not
    assert saved["status"] == "Not Assigned"
    assert tracked_item(client, pid, "Trap and Seal") == "In Progress"


def test_seal_complete_with_trap_incomplete_stays_in_progress(client):
    """The mirror image: the Seal half alone is not completion either."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-TRAPSEAL-3")
    task = get_task_by_name(client, pid, COS_STEP)

    fields = dict(SEAL_INPUTS)
    fields["seal_slides_loaded"] = "1"
    saved = save(client, task, fields)
    stored = _stored(client, task["task_id"])
    assert stored["seal_cos_pct"] == "48"
    assert not stored.get("trap_cos_pct")
    assert saved["status"] == "Not Assigned"


def test_both_cos_results_plus_the_checkbox_complete_the_step(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-TRAPSEAL-4")
    _thickness(client, pid)
    task = get_task_by_name(client, pid, COS_STEP)

    fields = dict(SEAL_INPUTS)
    fields.update({"sarah_quwarah_thickness_ft": "130", "seal_slides_loaded": "1"})
    saved = save(client, task, fields)

    assert saved["status"] == "Approved"
    assert tracked_item(client, pid, "Trap and Seal") == "Completed"
    engine = [row for row in history(client, task["task_id"])
              if row["action_type"] == "Field Completion"]
    assert len(engine) == 1


def test_both_results_without_the_checkbox_are_not_completion(client):
    """... and ticking it on a LATER save closes the step then."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-TRAPSEAL-5")
    _thickness(client, pid)
    task = get_task_by_name(client, pid, COS_STEP)

    fields = dict(SEAL_INPUTS)
    fields["sarah_quwarah_thickness_ft"] = "130"
    saved = save(client, task, fields)
    assert saved["status"] == "Not Assigned"

    saved = save(client, saved, {"seal_slides_loaded": "1"})
    assert saved["status"] == "Approved"


def test_unticking_the_seal_confirmation_reopens_the_step(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-TRAPSEAL-6")
    _thickness(client, pid)
    task = get_task_by_name(client, pid, COS_STEP)
    fields = dict(SEAL_INPUTS)
    fields.update({"sarah_quwarah_thickness_ft": "130", "seal_slides_loaded": "1"})
    approved = save(client, task, fields)
    assert approved["status"] == "Approved"

    reopened = save(client, approved, {"seal_slides_loaded": ""})

    assert reopened["status"] == "In Progress"
    assert tracked_item(client, pid, "Trap and Seal") == "In Progress"
    # Both stored results survive the reopen untouched -- the engine reconciles
    # STATUS, it never edits inputs.
    stored = _stored(client, task["task_id"])
    assert (stored["trap_cos_pct"], stored["seal_cos_pct"]) == ("80", "48")
    reopen = [row for row in history(client, task["task_id"])
              if row["action_type"] == "Field Reopen"]
    assert len(reopen) == 1


def test_the_lead_summary_cos_values_are_untouched_by_the_completion_rule(client):
    """Card 3B changes WHEN the step closes, never WHAT it reports.

    The lead summary reads Trap and Seal as separate values from the merged
    step's own EAV keys, and the Total Chance of Success stays the read-time
    Reservoir x Trap x Seal product -- identical before the confirmation is
    ticked, after it completes the step, and after unticking reopens it.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-TRAPSEAL-7")
    _thickness(client, pid)
    save(client, get_task_by_name(client, pid, "Reservoir CoS"),
         {"reservoir_cos_rows": json.dumps(RESERVOIR_ROWS), "reservoir_slides_loaded": "1"})
    task = get_task_by_name(client, pid, COS_STEP)
    fields = dict(SEAL_INPUTS)
    fields["sarah_quwarah_thickness_ft"] = "130"

    def summary():
        detail = client.get(f"/api/projects/{pid}/detail").get_json()
        merged = detail["fields"][COS_STEP]
        return (merged.get("trap_cos_pct"), merged.get("seal_cos_pct"),
                detail["overview"]["derisking"])

    saved = save(client, task, fields)                       # not yet confirmed
    before = summary()
    assert before[0] == "80" and before[1] == "48" and before[2]

    saved = save(client, saved, {"seal_slides_loaded": "1"})  # completes the step
    assert saved["status"] == "Approved"
    assert summary() == before

    save(client, saved, {"seal_slides_loaded": ""})           # reopens it
    assert summary() == before


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


# ---------------------------------------------------------------------------
# Card 2B -- the four tracked items of the consolidated Lead Assessment page
# ---------------------------------------------------------------------------
# The page is ONE workspace with ONE Save, but the STATUSES are still four
# independent tracked items: the Save PATCHes each owning task in turn, and the
# engine decides each item from the fields that item owns. These tests drive
# exactly that -- one PATCH per task, the real endpoint -- and assert on the
# board's own dots (tracked_item), not on internals.

AREA_STEP = "Area Definition"
THICKNESS_STEP = "Thickness Estimation"
GRV_STEP = "GRV Inputs"
RA_STEP = "Resource Assessment"

# A valid capture of each pair, matching the card's own worked example.
AREA_OK = {"p90_area_km2": "12.60", "p10_area_km2": "17.30"}
GRV_OK = {"grv_p90_thousand_acre_ft": "12.60", "grv_p10_thousand_acre_ft": "17.30"}
THICKNESS_OK = {"reservoir_thickness_ft": "200", "formation_thickness_ft": "500"}


def _save_step(client, pid, step, fields, expect=200):
    return save(client, get_task_by_name(client, pid, step), fields, expect=expect)


def test_card_2b_registers_all_four_lead_assessment_items(client):
    """The four steps are field-driven, and each names the keys it owns.

    The whole point of the consolidated page is that four items still complete
    independently -- so the table has to claim all four, and no two of them may
    key on the same field (that would make one item's Save move another's dot).
    """
    import workflow

    specs = {step: workflow.FIELD_COMPLETION[step]
             for step in (AREA_STEP, THICKNESS_STEP, GRV_STEP, RA_STEP)}
    claimed = []
    for spec in specs.values():
        claimed.extend(spec.get("required_checked", ()))
        claimed.extend(spec.get("required_present", ()))
    assert len(claimed) == len(set(claimed)), "no field gates two different items"
    # The TVDSS is stored on Area Definition but gates nothing, anywhere.
    assert "top_formation_tvdss_ft" not in claimed
    # Ordering is part of the rule, not an afterthought.
    assert specs[AREA_STEP]["required_greater"] == (("p10_area_km2", "p90_area_km2"),)
    assert specs[GRV_STEP]["required_greater"] == (
        ("grv_p10_thousand_acre_ft", "grv_p90_thousand_acre_ft"),)
    assert specs[THICKNESS_STEP]["required_greater"] == (
        ("formation_thickness_ft", "reservoir_thickness_ft"),)


def test_area_definition_completes_on_a_valid_p90_p10_pair(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-AREA")
    assert tracked_item(client, pid, AREA_STEP) != "Completed"

    task = _save_step(client, pid, AREA_STEP, AREA_OK)
    assert task["status"] == "Approved"
    assert tracked_item(client, pid, AREA_STEP) == "Completed"
    # And ONLY that item -- its three page-mates are untouched by this save.
    for other in (THICKNESS_STEP, GRV_STEP, RA_STEP):
        assert tracked_item(client, pid, other) != "Completed", other


def test_area_definition_needs_BOTH_percentiles(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-AREA-HALF")
    task = _save_step(client, pid, AREA_STEP, {"p90_area_km2": "12.60"})
    assert task["status"] != "Approved"
    assert tracked_item(client, pid, AREA_STEP) != "Completed"


def test_area_definition_rejects_a_zero_or_negative_percentile(client):
    """"0" is what a cleared input leaves behind -- non-blank, and not an area."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-AREA-ZERO")
    task = _save_step(client, pid, AREA_STEP, {"p90_area_km2": "0", "p10_area_km2": "17.30"})
    assert task["status"] != "Approved"
    task = _save_step(client, pid, AREA_STEP, {"p90_area_km2": "-3", "p10_area_km2": "17.30"})
    assert task["status"] != "Approved"
    assert tracked_item(client, pid, AREA_STEP) != "Completed"


def test_area_definition_rejects_an_equal_or_inverted_pair(client):
    """Equal percentiles are a mis-entry, and an inverted pair is never swapped."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-AREA-ORDER")
    task = _save_step(client, pid, AREA_STEP, {"p90_area_km2": "12.60", "p10_area_km2": "12.60"})
    assert task["status"] != "Approved", "P10 == P90 is not a distribution"
    task = _save_step(client, pid, AREA_STEP, {"p90_area_km2": "17.30", "p10_area_km2": "12.60"})
    assert task["status"] != "Approved", "and the server never reorders it for the user"
    assert tracked_item(client, pid, AREA_STEP) != "Completed"
    # Fix the order and it closes.
    assert _save_step(client, pid, AREA_STEP, AREA_OK)["status"] == "Approved"


def test_a_completed_area_definition_reopens_when_a_value_is_cleared(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-AREA-REOPEN")
    task = _save_step(client, pid, AREA_STEP, AREA_OK)
    assert task["status"] == "Approved"
    task = _save_step(client, pid, AREA_STEP, {"p10_area_km2": ""})
    assert task["status"] == "In Progress"
    assert tracked_item(client, pid, AREA_STEP) != "Completed"
    assert [row["action_type"] for row in history(client, task["task_id"])][-1] == "Field Reopen"


def test_the_tvdss_neither_completes_nor_reopens_area_definition(client):
    """Section 3's TVDSS is reference information, not a gate.

    It stores on the Area Definition task, so the risk is real: a rule that
    read every key of that task would make a blank TVDSS block an otherwise
    finished item, and a typed one close it early. Neither happens -- and the
    value is stored either way, including a NEGATIVE one (a subsea depth) that
    the positivity rule would have rejected had it applied.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-TVDSS")
    # (a) TVDSS alone completes nothing.
    task = _save_step(client, pid, AREA_STEP, {"top_formation_tvdss_ft": "-6500"})
    assert task["status"] != "Approved"
    # (b) a complete area pair completes the item WITHOUT any TVDSS...
    task = _save_step(client, pid, AREA_STEP, AREA_OK)
    assert task["status"] == "Approved"
    # (c) ...and changing the TVDSS afterwards does not reopen it. The negative
    # depth is stored verbatim -- never coerced, never rejected by the
    # positivity rule that guards this task's other two keys.
    assert _save_step(client, pid, AREA_STEP, {"top_formation_tvdss_ft": "-7100"})["status"] == "Approved"
    area_id = get_task_by_name(client, pid, AREA_STEP)["task_id"]
    assert _stored(client, area_id)["top_formation_tvdss_ft"] == "-7100"
    # (d) nor does clearing it.
    assert _save_step(client, pid, AREA_STEP, {"top_formation_tvdss_ft": ""})["status"] == "Approved"
    assert tracked_item(client, pid, AREA_STEP) == "Completed"


def test_grv_inputs_completes_on_a_valid_pair_and_reopens_on_an_inverted_one(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-GRV")
    assert _save_step(client, pid, GRV_STEP, {"grv_p90_thousand_acre_ft": "12.60"})["status"] != "Approved"
    assert _save_step(client, pid, GRV_STEP, GRV_OK)["status"] == "Approved"
    assert tracked_item(client, pid, GRV_STEP) == "Completed"
    inverted = _save_step(client, pid, GRV_STEP, {"grv_p10_thousand_acre_ft": "1.0"})
    assert inverted["status"] == "In Progress"
    assert tracked_item(client, pid, GRV_STEP) != "Completed"


def test_thickness_estimation_completes_on_both_rows_in_the_right_order(client):
    """The canonical FEET are the predicate -- whichever column the user typed."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-THICK")
    # A TWT capture alone proves nothing: the thicknesses are what every
    # downstream reader resolves.
    task = _save_step(client, pid, THICKNESS_STEP,
                      {"twt_reservoir_ms": "1500", "twt_formation_ms": "1800"})
    assert task["status"] != "Approved"
    # One row only.
    assert _save_step(client, pid, THICKNESS_STEP, {"reservoir_thickness_ft": "200"})["status"] != "Approved"
    # Both rows, correctly ordered.
    assert _save_step(client, pid, THICKNESS_STEP, THICKNESS_OK)["status"] == "Approved"
    assert tracked_item(client, pid, THICKNESS_STEP) == "Completed"


def test_thickness_estimation_rejects_an_equal_or_inverted_pair(client):
    """A formation no thicker than the reservoir inside it is not a capture."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-THICK-ORDER")
    task = _save_step(client, pid, THICKNESS_STEP,
                      {"reservoir_thickness_ft": "200", "formation_thickness_ft": "200"})
    assert task["status"] != "Approved"
    task = _save_step(client, pid, THICKNESS_STEP, {"formation_thickness_ft": "150"})
    assert task["status"] != "Approved"
    assert tracked_item(client, pid, THICKNESS_STEP) != "Completed"
    assert _save_step(client, pid, THICKNESS_STEP, THICKNESS_OK)["status"] == "Approved"


def test_the_twt_columns_and_source_marker_never_affect_completion(client):
    """Section 1's two-way times are stored beside the feet, and gate nothing.

    They are the other end of the SAME measurement (see
    config.TWT_THICKNESS_COEFFICIENTS): a lead whose conversion is not yet
    configured captures both columns by hand, and one whose conversion IS
    configured captures only one. Either way the step's completion has to read
    the feet alone, or the same lead would complete differently depending on a
    deployment setting.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-TWT")
    assert _save_step(client, pid, THICKNESS_STEP, THICKNESS_OK)["status"] == "Approved"
    for fields in ({"twt_reservoir_ms": "1500"}, {"twt_formation_ms": "1800"},
                   {"thickness_source_mode": "twt"}, {"twt_reservoir_ms": ""},
                   {"thickness_source_mode": ""}):
        assert _save_step(client, pid, THICKNESS_STEP, fields)["status"] == "Approved", fields
    assert tracked_item(client, pid, THICKNESS_STEP) == "Completed"


def test_resource_assessment_needs_the_checkbox_AND_a_stored_piip_mean(client):
    """Card 2B's Section 4 + Section 3 confirmation, both halves required.

    The mean is written by the page's AUTO-RUN through the fields-only endpoint
    (PATCH /api/tasks/<id>/dynamic-fields -- the same call the calculator's
    "Apply to Lead" used to make), and the box by the batched Save. This drives
    both routes, in the order that used to leave the item stuck: box first.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-RA")
    task = get_task_by_name(client, pid, RA_STEP)

    # (a) The confirmation alone: no volume to carry into the portfolio.
    task = save(client, task, {"polygons_surfaces_loaded": "1"})
    assert task["status"] != "Approved"
    assert tracked_item(client, pid, RA_STEP) != "Completed"

    # (b) The auto-run's write lands through the FIELDS-ONLY endpoint, and the
    # engine runs there too -- so the item closes without a second save.
    resp = client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields",
                        json={"fields": {"lead_piip_gas_mean": "19.4"}})
    assert resp.status_code == 200, resp.get_json()
    assert tracked_item(client, pid, RA_STEP) == "Completed"


def test_resource_assessment_piip_alone_is_not_completion(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-RA-PIIP")
    task = get_task_by_name(client, pid, RA_STEP)
    task = save(client, task, {"lead_piip_gas_mean": "19.4"})
    assert task["status"] != "Approved", "a volume whose surfaces were never filed is not reviewable"
    task = save(client, task, {"polygons_surfaces_loaded": "1"})
    assert task["status"] == "Approved"
    # Unticking reopens it, exactly like every other confirmation.
    task = save(client, task, {"polygons_surfaces_loaded": ""})
    assert task["status"] == "In Progress"
    assert tracked_item(client, pid, RA_STEP) != "Completed"


def test_a_blank_or_zero_piip_mean_does_not_complete_resource_assessment(client):
    """"0" BCF is what a failed run or a cleared field leaves behind."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-RA-ZERO")
    task = get_task_by_name(client, pid, RA_STEP)
    task = save(client, task, {"polygons_surfaces_loaded": "1", "lead_piip_gas_mean": "0"})
    assert task["status"] != "Approved"
    task = save(client, task, {"lead_piip_gas_mean": "19.4"})
    assert task["status"] == "Approved"


def test_the_whole_page_saved_step_by_step_turns_all_four_dots_green(client):
    """The end-to-end shape of one Save Updates press.

    The consolidated page groups its values by owning task and PATCHes each
    dirty one in turn; the auto-run has already written the PIIP mean. Four
    writes, four completed tracked items, one lead.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-PAGE")
    _save_step(client, pid, AREA_STEP, dict(AREA_OK, top_formation_tvdss_ft="-6500"))
    _save_step(client, pid, THICKNESS_STEP,
               dict(THICKNESS_OK, twt_reservoir_ms="1500", twt_formation_ms="1800",
                    thickness_source_mode=""))
    _save_step(client, pid, GRV_STEP, GRV_OK)
    ra = get_task_by_name(client, pid, RA_STEP)
    client.patch(f"/api/tasks/{ra['task_id']}/dynamic-fields",
                 json={"fields": {"lead_piip_gas_mean": "19.4"}})
    save(client, get_task_by_name(client, pid, RA_STEP), {"polygons_surfaces_loaded": "1"})

    for step in (AREA_STEP, THICKNESS_STEP, GRV_STEP, RA_STEP):
        assert tracked_item(client, pid, step) == "Completed", step
    # And the lead's headline volume is readable exactly where it always was.
    detail = client.get(f"/api/projects/{pid}/detail").get_json()
    assert detail["overview"]["lead_ogip"] == "19.4"


# ---------------------------------------------------------------------------
# Card 2B -- the config the page reads
# ---------------------------------------------------------------------------

def test_meta_serves_the_twt_thickness_coefficients(client):
    """The client reads the conversion from Store.meta, and it SHIPS EMPTY.

    Empty is the shipped state on purpose (config.TWT_THICKNESS_COEFFICIENTS):
    until an owner supplies calibrated coefficients, Section 1 is two manual
    inputs plus a pending note rather than a guessed derivation. The KEY has to
    be present regardless -- an absent key and an empty map must not be two
    different client states.
    """
    import config

    meta = client.get("/api/meta").get_json()
    assert "twt_thickness_coefficients" in meta
    assert meta["twt_thickness_coefficients"] == config.TWT_THICKNESS_COEFFICIENTS
    assert config.TWT_THICKNESS_COEFFICIENTS == {}, "ships empty; populate per deployment"


def test_the_polygons_folder_resolves_under_the_LEADS_share(client):
    """Card 2B's folder row: <leads share>\\<field>\\<lead>\\Polygons__Surfaces.

    It is a LEAD deliverable, so it belongs beside the prospect-step component
    folders on the Leads share -- not on the Wells share every other
    WELL_OVERVIEW_DIRECTORY_MAP section resolves to, and not on the separate
    Lead_Workflow share the Task Update stage buttons use.
    """
    import config

    pid = create_project(client, "WWWW-44")
    info = client.get(f"/api/projects/{pid}/folders/polygons").get_json()
    assert info["unc_path"] == "\\".join(
        [config.WINDOWS_LEAD_COMPONENT_SHARE_ROOT, "WWWW", "WWWW-44", "Polygons__Surfaces"])
    assert info["section"] == "Polygons & Surfaces"
    assert info["server_path"].startswith(str(config.LEAD_COMPONENT_DIRECTORY_ROOT))
    # The pre-existing sections are unmoved by the new root-selection bucket.
    assert client.get(f"/api/projects/{pid}/folders/well").get_json()["unc_path"].startswith(
        config.WINDOWS_WELL_SHARE_ROOT)
    assert client.get(f"/api/projects/{pid}/folders/risking_workflow").get_json()["unc_path"].startswith(
        config.WINDOWS_LEAD_WORKFLOW_SHARE_ROOT)


# ---------------------------------------------------------------------------
# reconcile=False -- the bulk writers' opt-out
# ---------------------------------------------------------------------------
# save_task_dynamic_fields reconciles by default (that is what lets card 2B's
# auto-run complete the Resource Assessment item through the fields-only
# endpoint). Bulk writers -- import_excel, seed_dev, and the submit-gate tick
# inside the approval walk -- must NOT get that: they lay down a PARTIAL field
# set and drive the status explicitly afterwards, so a step is legitimately
# Approved-with-unmet-predicate for the duration of the write, and the engine's
# reopen branch would knock it back open mid-import.

def _direct_save(client, task_id, fields, **kwargs):
    """Call save_task_dynamic_fields the way a bulk writer does (no HTTP)."""
    import db
    import workflow

    session = db.new_session()
    try:
        workflow.save_task_dynamic_fields(session, task_id, fields,
                                          changed_by="Bulk Writer", **kwargs)
    finally:
        session.close()


def test_a_bulk_write_does_not_reopen_an_approved_step(client):
    """reconcile=False leaves an Approved step alone, predicate or no predicate."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-BULK-NOREOPEN")
    task = _save_step(client, pid, AREA_STEP, AREA_OK)
    assert task["status"] == "Approved"

    # A partial bulk write that BREAKS the predicate outright.
    _direct_save(client, task["task_id"], {"p10_area_km2": ""}, reconcile=False)
    assert get_task_by_name(client, pid, AREA_STEP)["status"] == "Approved"
    assert tracked_item(client, pid, AREA_STEP) == "Completed"
    # ...and no engine event was logged either -- the hook never ran.
    assert "Field Reopen" not in [row["action_type"] for row in history(client, task["task_id"])]


def test_a_bulk_write_does_not_complete_an_open_step_either(client):
    """The opt-out is symmetric: it suppresses the CLOSE branch too.

    An importer that writes a complete field set and then walks the step to
    Approved itself must not have the engine close it first -- the walk is the
    audit trail the import intends to leave.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-BULK-NOCLOSE")
    task = get_task_by_name(client, pid, AREA_STEP)
    _direct_save(client, task["task_id"], AREA_OK, reconcile=False)
    assert get_task_by_name(client, pid, AREA_STEP)["status"] != "Approved"
    assert tracked_item(client, pid, AREA_STEP) != "Completed"


def test_the_default_still_reconciles_on_the_same_write(client):
    """The paired control: the SAME two writes, with the default reconcile.

    This is the card 2B path (the auto-run's PATCH /dynamic-fields), so the
    difference between the two tests above and this one is exactly the keyword.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-BULK-CONTROL")
    task = get_task_by_name(client, pid, AREA_STEP)
    # Closes...
    _direct_save(client, task["task_id"], AREA_OK)
    assert get_task_by_name(client, pid, AREA_STEP)["status"] == "Approved"
    assert tracked_item(client, pid, AREA_STEP) == "Completed"
    # ...and reopens.
    _direct_save(client, task["task_id"], {"p10_area_km2": ""})
    assert get_task_by_name(client, pid, AREA_STEP)["status"] == "In Progress"
    assert "Field Reopen" in [row["action_type"] for row in history(client, task["task_id"])]


def test_the_http_fields_endpoint_keeps_the_reconciling_default(client):
    """The route must not have been switched to the bulk behaviour by accident."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-BULK-ROUTE")
    task = get_task_by_name(client, pid, RA_STEP)
    save(client, task, {"polygons_surfaces_loaded": "1"})
    resp = client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields",
                        json={"fields": {"lead_piip_gas_mean": "19.4"}})
    assert resp.status_code == 200, resp.get_json()
    assert tracked_item(client, pid, RA_STEP) == "Completed"


def test_the_submit_gate_tick_inside_the_approval_walk_does_not_reconcile(client):
    """satisfy_submit_gate writes MID-WALK, between the submit and the approve.

    Its step ("SAD Update") is not field-driven today, so this pins the wiring
    rather than a behaviour change: the walk still lands Approved, and it is the
    walk's own events -- not a Field Completion -- that got it there.
    """
    import db
    import workflow

    login(client, SUPERVISOR)
    pid = create_project(client, "FC-BULK-GATE", business_plan_enabled=1,
                         business_plan_year=2027)
    task = get_task_by_name(client, pid, "SAD Update")
    session = db.new_session()
    try:
        workflow.ensure_task_approved(session, task["task_id"], SUPERVISOR)
    finally:
        session.close()
    assert get_task_by_name(client, pid, "SAD Update")["status"] == "Approved"
    events = [row["action_type"] for row in history(client, task["task_id"])]
    assert "Component Approved" in events
    assert "Field Completion" not in events
