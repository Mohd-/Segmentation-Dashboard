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
    """Rules name live steps, except the four deliberate checkpoint labels."""
    import workflow
    from workflow.constants import (FIELD_COMPLETION_AUTOMATED_STEPS,
                                    LEAD_ASSESSMENT_CHECKPOINTS)

    active = {name for _seq, name, _stage in workflow.PIPELINE_TEMPLATES}
    checkpoints = set(LEAD_ASSESSMENT_CHECKPOINTS)
    assert set(workflow.FIELD_COMPLETION) <= active | checkpoints
    assert checkpoints.isdisjoint(active)
    # ASAS owner decision: the consolidated Lead Assessment row is automated
    # like every other field-driven card -- its aggregate predicate closes it.
    assert "Lead Assessment" in FIELD_COMPLETION_AUTOMATED_STEPS
    assert FIELD_COMPLETION_AUTOMATED_STEPS <= active | checkpoints
    assert FIELD_COMPLETION_AUTOMATED_STEPS & active == set(workflow.FIELD_COMPLETION) & active


def test_auto_approve_policy_is_the_prospect_template_minus_segmentation_slides():
    """THE ASAS OWNER DECISION, pinned as data.

    "No approval is required for all segment maturation steps except
    segmentation slides." The policy set is DERIVED from PIPELINE_TEMPLATES'
    prospect stage groups (never a second hand-typed list), Segmentation
    Slides keeps its human approval, and the BP execution pipeline is
    untouched. Every policy step carries a FIELD_COMPLETION predicate (the
    GeoX assessment gained its stored-mean rule with the same decision), so
    no maturation step is left needing the hidden supervisor walk.
    """
    import workflow
    from workflow.constants import (AUTO_APPROVE_ON_SAVE_STEPS,
                                    FIELD_COMPLETION_AUTOMATED_STEPS,
                                    LEAD_ASSESSMENT_CHECKPOINTS)

    prospect = {name for _seq, name, stage in workflow.PIPELINE_TEMPLATES
                if stage in workflow.PROSPECT_STAGES}
    bp = {name for _seq, name, stage in workflow.PIPELINE_TEMPLATES
          if stage in workflow.BP_EXECUTION_STAGES}
    assert AUTO_APPROVE_ON_SAVE_STEPS == prospect - {"Segmentation Slides"}
    assert AUTO_APPROVE_ON_SAVE_STEPS.isdisjoint(bp)
    assert AUTO_APPROVE_ON_SAVE_STEPS.isdisjoint(
        workflow.FIELD_COMPLETION_MANUAL_APPROVAL_STEPS)
    # Every policy step has a predicate: no step is left uncompletable now
    # that the UI hides the manual walk for all of them.
    assert AUTO_APPROVE_ON_SAVE_STEPS - set(workflow.FIELD_COMPLETION) == set()
    # Everything the engine automates is either a policy step or a v7
    # checkpoint label (legacy inactive rows keep reconciling) -- never a BP
    # step, never a manual-approval step.
    assert FIELD_COMPLETION_AUTOMATED_STEPS <= (
        AUTO_APPROVE_ON_SAVE_STEPS | set(LEAD_ASSESSMENT_CHECKPOINTS))


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
    assert task["status"] == "In Progress"
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
    # "Component Assigned" comes FIRST now: creation auto-assignment assigned
    # the step (In Progress) before any save, so the engine's walk resumes at
    # submit -> approve with no assign leg of its own.
    assert [row["action_type"] for row in events if row["action_type"].startswith("Component ")] == [
        "Component Assigned", "Component Inputs Updated", "Component Submitted", "Component Approved",
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
    assert saved["status"] == "In Progress"
    assert tracked_item(client, pid, "Reservoir") == "In Progress"

    # An explicitly EMPTY sheet is stored as the non-blank string "[]" -- still
    # no result, still not complete.
    saved = save(client, saved, {"reservoir_slides_loaded": "1", "reservoir_cos_rows": json.dumps([])})
    assert saved["status"] == "In Progress"


def test_reservoir_rows_alone_are_not_completion(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-RESERVOIR-2")
    task = get_task_by_name(client, pid, "Reservoir CoS")

    saved = save(client, task, {"reservoir_cos_rows": json.dumps(RESERVOIR_ROWS)})
    stored = client.get(f"/api/tasks/{task['task_id']}/dynamic-fields").get_json()
    assert json.loads(stored["reservoir_cos_rows"])[0]["reservoir_cos_pct"]  # scored
    assert saved["status"] == "In Progress"
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
    save(client, get_task_by_name(client, pid, "Lead Assessment"),
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
    assert saved["status"] == "In Progress"
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
    assert saved["status"] == "In Progress"
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
    assert saved["status"] == "In Progress"


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
    assert saved["status"] == "In Progress"

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
    other = get_task_by_name(client, pid, "Lead Assessment")
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
# The page is one workspace, one Save and one lifecycle row. The four stable
# labels below are CHECKPOINTS derived independently from fields on that row.
# Since the ASAS owner decision the row itself is engine-automated: it closes
# when ALL FOUR checkpoints are satisfied (never a save sooner) and reopens
# when a later save of the page breaks one.

LEAD_STEP = "Lead Assessment"
AREA_STEP = "Area Definition"
THICKNESS_STEP = "Thickness Estimation"
GRV_STEP = "GRV Inputs"
RA_STEP = "Resource Assessment"

# A valid capture of each pair, matching the card's own worked example.
AREA_OK = {"p90_area_km2": "12.60", "p10_area_km2": "17.30"}
GRV_OK = {"grv_p90_thousand_acre_ft": "12.60", "grv_p10_thousand_acre_ft": "17.30"}
THICKNESS_OK = {"reservoir_thickness_ft": "200", "formation_thickness_ft": "500"}


def _save_step(client, pid, step, fields, expect=200):
    task_name = LEAD_STEP if step in (AREA_STEP, THICKNESS_STEP, GRV_STEP, RA_STEP) else step
    return save(client, get_task_by_name(client, pid, task_name), fields, expect=expect)


def _assert_lead_lifecycle_not_field_driven(client, pid, expected="In Progress"):
    # "In Progress" is a fresh logged-in lead's creation state now: the
    # creation auto-assignment assigns every prospect step to its creator
    # (or a configured rule assignee), which moves it out of Not Assigned.
    task = get_task_by_name(client, pid, LEAD_STEP)
    assert task["status"] == expected
    assert not [row for row in history(client, task["task_id"])
                if row["action_type"] in ("Field Completion", "Field Reopen")]


def test_card_2b_registers_four_checkpoints_and_one_automated_aggregate(client):
    """Four checkpoint predicates feed one automated aggregate task rule.

    The whole point of the consolidated page is that four items still complete
    independently -- so the table has to claim all four, and no two of them may
    key on the same field (that would make one item's Save move another's dot).
    Since the ASAS owner decision the aggregate row is engine-automated too:
    all four checkpoints complete IS the approval.
    """
    import workflow
    from workflow.constants import FIELD_COMPLETION_AUTOMATED_STEPS

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
    aggregate = workflow.FIELD_COMPLETION[LEAD_STEP]
    assert set(aggregate["required_present"]) == {
        key for spec in specs.values() for key in spec.get("required_present", ())}
    assert LEAD_STEP in FIELD_COMPLETION_AUTOMATED_STEPS


def test_inactive_legacy_assessment_fields_remain_readable_but_do_not_drive_v7_checkpoints(client):
    """Retired buckets stay in /detail for audit/back-compat; v7 state is canonical."""
    pid = create_project(client, "FC-2B-LEGACY-READ")
    conn = raw_sqlite_connect(client.db_path)
    try:
        cur = conn.execute(
            "INSERT INTO project_tasks (project_id, sequence_no, task_name, stage_group, "
            "status, priority, is_active) VALUES (?, 1, ?, 'Lead Assessment', "
            "'Approved', 'Low', 0)", (pid, AREA_STEP))
        legacy_id = cur.lastrowid
        for key, value in AREA_OK.items():
            conn.execute(
                "INSERT INTO task_dynamic_fields (task_id, field_key, field_value) VALUES (?, ?, ?)",
                (legacy_id, key, value))
        conn.commit()
    finally:
        conn.close()

    detail = client.get(f"/api/projects/{pid}/detail").get_json()
    assert detail["fields"][AREA_STEP] == AREA_OK
    assert tracked_item(client, pid, AREA_STEP) == "Not Started"

    _save_step(client, pid, AREA_STEP, AREA_OK)
    assert tracked_item(client, pid, AREA_STEP) == "Completed"
    assert client.get(f"/api/projects/{pid}/detail").get_json()["fields"][AREA_STEP] == AREA_OK


def test_area_definition_completes_on_a_valid_p90_p10_pair(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-AREA")
    assert tracked_item(client, pid, AREA_STEP) != "Completed"

    task = _save_step(client, pid, AREA_STEP, AREA_OK)
    assert task["status"] == "In Progress"
    assert tracked_item(client, pid, AREA_STEP) == "Completed"
    # And ONLY that item -- its three page-mates are untouched by this save.
    for other in (THICKNESS_STEP, GRV_STEP, RA_STEP):
        assert tracked_item(client, pid, other) != "Completed", other


def test_area_definition_needs_BOTH_percentiles(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-AREA-HALF")
    task = _save_step(client, pid, AREA_STEP, {"p90_area_km2": "12.60"})
    assert task["status"] == "In Progress"
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
    assert _save_step(client, pid, AREA_STEP, AREA_OK)["status"] == "In Progress"
    assert tracked_item(client, pid, AREA_STEP) == "Completed"


def test_a_completed_area_checkpoint_reopens_without_reopening_the_lifecycle(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-AREA-REOPEN")
    task = _save_step(client, pid, AREA_STEP, AREA_OK)
    assert task["status"] == "In Progress"
    task = _save_step(client, pid, AREA_STEP, {"p10_area_km2": ""})
    assert task["status"] == "In Progress"
    assert tracked_item(client, pid, AREA_STEP) != "Completed"
    _assert_lead_lifecycle_not_field_driven(client, pid)


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
    assert task["status"] == "In Progress"
    # (b) a complete area pair completes the item WITHOUT any TVDSS...
    task = _save_step(client, pid, AREA_STEP, AREA_OK)
    assert task["status"] == "In Progress"
    # (c) ...and changing the TVDSS afterwards does not reopen it. The negative
    # depth is stored verbatim -- never coerced, never rejected by the
    # positivity rule that guards this task's other two keys.
    assert _save_step(client, pid, AREA_STEP, {"top_formation_tvdss_ft": "-7100"})["status"] == "In Progress"
    area_id = get_task_by_name(client, pid, LEAD_STEP)["task_id"]
    assert _stored(client, area_id)["top_formation_tvdss_ft"] == "-7100"
    # (d) nor does clearing it.
    assert _save_step(client, pid, AREA_STEP, {"top_formation_tvdss_ft": ""})["status"] == "In Progress"
    assert tracked_item(client, pid, AREA_STEP) == "Completed"


def test_grv_inputs_completes_on_a_valid_pair_and_reopens_on_an_inverted_one(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-GRV")
    assert _save_step(client, pid, GRV_STEP, {"grv_p90_thousand_acre_ft": "12.60"})["status"] != "Approved"
    assert _save_step(client, pid, GRV_STEP, GRV_OK)["status"] == "In Progress"
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
    assert _save_step(client, pid, THICKNESS_STEP, THICKNESS_OK)["status"] == "In Progress"
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
    assert _save_step(client, pid, THICKNESS_STEP, THICKNESS_OK)["status"] == "In Progress"
    assert tracked_item(client, pid, THICKNESS_STEP) == "Completed"


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
    assert _save_step(client, pid, THICKNESS_STEP, THICKNESS_OK)["status"] == "In Progress"
    for fields in ({"twt_reservoir_ms": "1500"}, {"twt_formation_ms": "1800"},
                   {"thickness_source_mode": "twt"}, {"twt_reservoir_ms": ""},
                   {"thickness_source_mode": ""}):
        assert _save_step(client, pid, THICKNESS_STEP, fields)["status"] == "In Progress", fields
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
    task = get_task_by_name(client, pid, LEAD_STEP)

    # (a) The confirmation alone: no volume to carry into the portfolio.
    task = save(client, task, {"polygons_surfaces_loaded": "1"})
    assert task["status"] == "In Progress"
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
    task = get_task_by_name(client, pid, LEAD_STEP)
    task = save(client, task, {"lead_piip_gas_mean": "19.4"})
    assert task["status"] == "In Progress", "field writes do not advance the lifecycle"
    task = save(client, task, {"polygons_surfaces_loaded": "1"})
    assert task["status"] == "In Progress"
    assert tracked_item(client, pid, RA_STEP) == "Completed"
    # Unticking clears the checkpoint but does not reopen the lifecycle.
    task = save(client, task, {"polygons_surfaces_loaded": ""})
    assert task["status"] == "In Progress"
    assert tracked_item(client, pid, RA_STEP) != "Completed"


def test_a_blank_or_zero_piip_mean_does_not_complete_resource_assessment(client):
    """"0" BCF is what a failed run or a cleared field leaves behind."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-RA-ZERO")
    task = get_task_by_name(client, pid, LEAD_STEP)
    task = save(client, task, {"polygons_surfaces_loaded": "1", "lead_piip_gas_mean": "0"})
    assert task["status"] == "In Progress"
    task = save(client, task, {"lead_piip_gas_mean": "19.4"})
    assert task["status"] == "In Progress"
    assert tracked_item(client, pid, RA_STEP) == "Completed"


def _stage_counter(client, pid, stage):
    """The detail sidebar's x/4 for one stage, computed the way the client is.

    views/detail.js leadStageGroups groups the SERVER's tracked_items by
    ``stage`` and counts ``status == 'Completed'``; this reproduces exactly that
    arithmetic over the same payload, so the assertion is about the number the
    user reads rather than about a task row.
    """
    rows = client.get("/api/projects?pipeline_filter=prospect").get_json()
    items = [i for i in next(r for r in rows if r["project_id"] == pid)["tracked_items"]
             if i["stage"] == stage]
    return sum(1 for i in items if i["status"] == "Completed"), len(items)


def test_a_box_model_lead_shows_piip_but_lead_assessment_stays_three_of_four(client):
    """MIGRATION-CARE CONSTRAINT: GRV is still TRACKED, not absorbed.

    Card 2B derives PIIP from area x thickness when the GRV percentiles are
    blank (the "Box Model" branch), so a lead can carry a perfectly good
    volume -- and a Completed Resource Assessment -- while nobody has entered
    GRV. The temptation the constraint forbids is treating that as a finished
    Lead Assessment: GRV Inputs is one of the twelve tracked items, so the
    stage must read 3/4 and the lead must NOT be at 4/12.

    Drives the real endpoints in the order the page does: the auto-run's
    fields-only PATCH writes the mean, the batched Save writes the rest.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-BOXMODEL")

    _save_step(client, pid, AREA_STEP, AREA_OK)
    _save_step(client, pid, THICKNESS_STEP, THICKNESS_OK)
    # The box-model auto-run: a PIIP mean with NO GRV percentiles anywhere.
    ra = get_task_by_name(client, pid, LEAD_STEP)
    resp = client.patch(f"/api/tasks/{ra['task_id']}/dynamic-fields",
                        json={"fields": {"lead_piip_gas_mean": "19.4",
                                         "lead_calculation_method": "Box Model"}})
    assert resp.status_code == 200, resp.get_json()
    save(client, get_task_by_name(client, pid, LEAD_STEP), {"polygons_surfaces_loaded": "1"})

    # The lead really does carry a volume, readable where the portfolio reads it.
    assert client.get(f"/api/projects/{pid}/detail").get_json()["overview"]["lead_ogip"] == "19.4"
    # Three of the four items are done...
    for step in (AREA_STEP, THICKNESS_STEP, RA_STEP):
        assert tracked_item(client, pid, step) == "Completed", step
    # ...and GRV Inputs is emphatically NOT one of them.
    assert tracked_item(client, pid, GRV_STEP) != "Completed"
    merged = get_task_by_name(client, pid, LEAD_STEP)
    assert merged["status"] == "In Progress"
    merged_fields = client.get(f"/api/tasks/{merged['task_id']}/dynamic-fields").get_json()
    assert not merged_fields.get("grv_p90_thousand_acre_ft")
    assert not merged_fields.get("grv_p10_thousand_acre_ft")

    # The counter the user actually reads: 3/4, not 4/4.
    assert _stage_counter(client, pid, "Lead Assessment") == (3, 4)
    # And the lead's completion is 3/12, never 4/12 -- one shared denominator.
    assert client.get(f"/api/projects/{pid}/completion").get_json()["percent"] == round(3 / 12 * 100, 1)

    # Entering the GRV pair afterwards is what closes the fourth item.
    _save_step(client, pid, GRV_STEP, GRV_OK)
    assert _stage_counter(client, pid, "Lead Assessment") == (4, 4)


def test_the_whole_page_one_save_turns_all_four_dots_green_and_auto_approves(client):
    """The end-to-end shape of one Save Updates press.

    The consolidated page PATCHes one task; the auto-run writes the PIIP mean
    through the fields-only endpoint. Four completed checkpoints -- and, since
    the ASAS owner decision, the write that satisfies the LAST of them closes
    the one lifecycle row itself: no supervisor click, one engine event,
    whichever of the two save paths lands it.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-PAGE")
    captured = dict(AREA_OK, **THICKNESS_OK, **GRV_OK,
                    top_formation_tvdss_ft="-6500", twt_reservoir_ms="1500",
                    twt_formation_ms="1800", thickness_source_mode="",
                    polygons_surfaces_loaded="1")
    _save_step(client, pid, LEAD_STEP, captured)
    # Three of four checkpoints: the aggregate is unmet, so nothing moved yet.
    _assert_lead_lifecycle_not_field_driven(client, pid)
    ra = get_task_by_name(client, pid, LEAD_STEP)
    client.patch(f"/api/tasks/{ra['task_id']}/dynamic-fields",
                 json={"fields": {"lead_piip_gas_mean": "19.4"}})

    for step in (AREA_STEP, THICKNESS_STEP, GRV_STEP, RA_STEP):
        assert tracked_item(client, pid, step) == "Completed", step
    # And the lead's headline volume is readable exactly where it always was.
    detail = client.get(f"/api/projects/{pid}/detail").get_json()
    assert detail["overview"]["lead_ogip"] == "19.4"
    merged = get_task_by_name(client, pid, LEAD_STEP)
    assert merged["status"] == "Approved"
    engine = [row for row in history(client, merged["task_id"])
              if row["action_type"] == "Field Completion"]
    assert len(engine) == 1
    assert engine[0]["comment"] == "Completed: required confirmations satisfied"
    assert engine[0]["changed_by"] == SUPERVISOR

    # IDEMPOTENT: replaying the same page save adds no second approval/event.
    save(client, merged, captured)
    assert get_task_by_name(client, pid, LEAD_STEP)["status"] == "Approved"
    assert len([row for row in history(client, merged["task_id"])
                if row["action_type"] == "Field Completion"]) == 1


def test_breaking_one_checkpoint_reopens_the_auto_approved_lead_assessment(client):
    """The reconcile is symmetric: clearing a required field on a later save of
    the page reopens the auto-approved row, with its own audited event."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-UNAPPROVE")
    captured = dict(AREA_OK, **THICKNESS_OK, **GRV_OK,
                    polygons_surfaces_loaded="1", lead_piip_gas_mean="19.4")
    approved = _save_step(client, pid, LEAD_STEP, captured)
    assert approved["status"] == "Approved"

    reopened = save(client, approved, {"p10_area_km2": ""})
    assert reopened["status"] == "In Progress"
    assert tracked_item(client, pid, AREA_STEP) != "Completed"
    events = history(client, approved["task_id"])
    reopen = [row for row in events if row["action_type"] == "Field Reopen"]
    assert len(reopen) == 1
    assert reopen[0]["comment"] == "Reopened: required confirmation removed"
    # Restoring the field closes it again -- the engine is a reconciliation.
    assert save(client, reopened, AREA_OK)["status"] == "Approved"


def test_approved_lead_assessment_reconciles_only_on_its_own_save(client):
    """The grandfather rule now covers Lead Assessment like every other step.

    A manually-approved row (the transition endpoint stays functional for old
    clients) keeps its approval until ITS OWN form is next saved; that save is
    the user choosing the field state on screen, and the engine reconciles the
    status to it -- here, to In Progress, because the aggregate is unmet.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-2B-LIFECYCLE")
    approved = drive_to_approved(client, pid, LEAD_STEP)
    assert approved["status"] == "Approved"
    # Reads and sibling saves never touch it (the grandfather rule proper).
    save(client, get_task_by_name(client, pid, "Reservoir CoS"),
         {"reservoir_slides_loaded": "1"})
    client.get(f"/api/projects/{pid}/detail").get_json()
    assert get_task_by_name(client, pid, LEAD_STEP)["status"] == "Approved"
    # Its own save, aggregate unmet -> reopen.
    reopened = save(client, get_task_by_name(client, pid, LEAD_STEP), AREA_OK)
    assert reopened["status"] == "In Progress"
    assert [row["action_type"] for row in history(client, approved["task_id"])
            if row["action_type"] in ("Field Completion", "Field Reopen")] == ["Field Reopen"]


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
    task = _save_step(client, pid, "Seismic Signature Validation", {"seismic_slides_loaded": "1"})
    assert task["status"] == "Approved"

    # A partial bulk write that BREAKS the predicate outright.
    _direct_save(client, task["task_id"], {"seismic_slides_loaded": ""}, reconcile=False)
    assert get_task_by_name(client, pid, "Seismic Signature Validation")["status"] == "Approved"
    assert tracked_item(client, pid, "Seismic Validation") == "Completed"
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
    task = get_task_by_name(client, pid, "Seismic Signature Validation")
    _direct_save(client, task["task_id"], {"seismic_slides_loaded": "1"}, reconcile=False)
    assert get_task_by_name(client, pid, "Seismic Signature Validation")["status"] != "Approved"
    assert tracked_item(client, pid, "Seismic Validation") != "Completed"


def test_the_default_still_reconciles_on_the_same_write(client):
    """The paired control: the SAME two writes, with the default reconcile.

    The difference between the two tests above and this one is exactly the keyword.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-BULK-CONTROL")
    task = get_task_by_name(client, pid, "Seismic Signature Validation")
    # Closes...
    _direct_save(client, task["task_id"], {"seismic_slides_loaded": "1"})
    assert get_task_by_name(client, pid, "Seismic Signature Validation")["status"] == "Approved"
    assert tracked_item(client, pid, "Seismic Validation") == "Completed"
    # ...and reopens.
    _direct_save(client, task["task_id"], {"seismic_slides_loaded": ""})
    assert get_task_by_name(client, pid, "Seismic Signature Validation")["status"] == "In Progress"
    assert "Field Reopen" in [row["action_type"] for row in history(client, task["task_id"])]


def test_the_http_fields_endpoint_updates_the_resource_checkpoint_without_lifecycle_reconciliation(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-BULK-ROUTE")
    task = get_task_by_name(client, pid, LEAD_STEP)
    save(client, task, {"polygons_surfaces_loaded": "1"})
    resp = client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields",
                        json={"fields": {"lead_piip_gas_mean": "19.4"}})
    assert resp.status_code == 200, resp.get_json()
    assert tracked_item(client, pid, RA_STEP) == "Completed"
    _assert_lead_lifecycle_not_field_driven(client, pid)


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


# ---------------------------------------------------------------------------
# Card 4A -- Moving Tolerance (eight fields, all eight required)
# ---------------------------------------------------------------------------
# The staking tolerance is one capture: the lead's X/Y plus THREE complete
# max-distance/azimuth option pairs. Anything less still SAVES -- a partial
# capture is a normal work-in-progress save -- it just leaves the item In
# Progress, which is what a half-filled pair should read as.

TOLERANCE_STEP = "Moving Tolerance"

TOLERANCE_LOCATION = {"staking_well_x": "532100.5", "staking_well_y": "2895120.1"}
TOLERANCE_OPTIONS = {
    "staking_opt1_max_distance_m": "150", "staking_opt1_azimuth_deg": "45",
    "staking_opt2_max_distance_m": "220", "staking_opt2_azimuth_deg": "180",
    "staking_opt3_max_distance_m": "90", "staking_opt3_azimuth_deg": "310",
}
TOLERANCE_OK = dict(TOLERANCE_LOCATION, **TOLERANCE_OPTIONS)


def test_card_4a_registers_moving_tolerance_with_all_eight_fields(client):
    """The declarative spec IS the card: eight required keys, no ordering rule.

    Deliberately no ``required_greater`` and no range rule -- the card is
    explicit that this step gains a completion rule and nothing else. A
    distance/azimuth pair has no natural ordering (they are not percentiles),
    and an azimuth range would be a NEW constraint on production data.
    """
    import workflow

    spec = workflow.FIELD_COMPLETION[TOLERANCE_STEP]
    assert set(spec) == {"required_present"}, "no checkbox and no ordering half"
    assert list(spec["required_present"]) == [
        "staking_well_x", "staking_well_y",
        "staking_opt1_max_distance_m", "staking_opt1_azimuth_deg",
        "staking_opt2_max_distance_m", "staking_opt2_azimuth_deg",
        "staking_opt3_max_distance_m", "staking_opt3_azimuth_deg",
    ]
    # The eight are NUMERIC (not positive-only): an azimuth of 0 is due north.
    assert set(spec["required_present"]) <= workflow.NUMERIC_FIELDS
    assert not (set(spec["required_present"]) & workflow.POSITIVE_NUMBER_FIELDS)


def test_moving_tolerance_completes_only_on_all_eight_fields(client):
    """The completion matrix: 0 -> 4 -> 7 -> 8 fields."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4A-MATRIX")
    task = get_task_by_name(client, pid, TOLERANCE_STEP)

    # 0 of 8 -- a fresh step.
    assert tracked_item(client, pid, TOLERANCE_STEP) != "Completed"

    # 4 of 8 -- the location and one whole option pair.
    task = save(client, task, dict(TOLERANCE_LOCATION,
                                   staking_opt1_max_distance_m="150",
                                   staking_opt1_azimuth_deg="45"))
    assert task["status"] != "Approved"
    assert tracked_item(client, pid, TOLERANCE_STEP) != "Completed"

    # 7 of 8 -- everything but the last azimuth. STILL not complete: two
    # directions to move a rig in is not the tolerance the surveyors are owed.
    seven = dict(TOLERANCE_OK)
    seven["staking_opt3_azimuth_deg"] = ""
    task = save(client, task, seven)
    assert task["status"] != "Approved"

    # 8 of 8.
    task = save(client, task, TOLERANCE_OK)
    assert task["status"] == "Approved"
    assert tracked_item(client, pid, TOLERANCE_STEP) == "Completed"


def test_one_filled_member_of_a_pair_saves_but_does_not_complete(client):
    """A half pair is SAVED (the value survives) and the item stays open."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4A-HALFPAIR")
    task = get_task_by_name(client, pid, TOLERANCE_STEP)
    half = dict(TOLERANCE_OK)
    half["staking_opt2_azimuth_deg"] = ""
    task = save(client, task, half)
    assert task["status"] != "Approved"
    # The partial save is a real write: the distance the user typed is stored.
    fields = client.get(f"/api/tasks/{task['task_id']}/dynamic-fields").get_json()
    assert fields["staking_opt2_max_distance_m"] == "220"
    assert fields.get("staking_opt2_azimuth_deg", "") == ""


def test_a_zero_azimuth_is_due_north_and_completes_the_step(client):
    """The NUMERIC_FIELDS choice, end to end.

    A positive-only rule (POSITIVE_NUMBER_FIELDS) would refuse to complete a
    perfectly ordinary capture, because 0 degrees is due north -- and a max
    distance of 0 is a rig that may not move, which is a decision, not a blank.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4A-ZERO")
    task = get_task_by_name(client, pid, TOLERANCE_STEP)
    zeroed = dict(TOLERANCE_OK, staking_opt1_azimuth_deg="0", staking_opt3_max_distance_m="0")
    task = save(client, task, zeroed)
    assert task["status"] == "Approved"


def test_a_non_numeric_coordinate_is_an_absent_one(client):
    """"TBD" in a coordinate box is not a coordinate."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4A-TBD")
    task = get_task_by_name(client, pid, TOLERANCE_STEP)
    task = save(client, task, dict(TOLERANCE_OK, staking_well_x="TBD"))
    assert task["status"] != "Approved"
    task = save(client, task, {"staking_well_x": "532100.5"})
    assert task["status"] == "Approved"


def test_clearing_one_tolerance_field_reopens_the_completed_step(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4A-REOPEN")
    task = save(client, get_task_by_name(client, pid, TOLERANCE_STEP), TOLERANCE_OK)
    assert task["status"] == "Approved"
    task = save(client, task, {"staking_opt2_max_distance_m": ""})
    assert task["status"] == "In Progress"
    assert tracked_item(client, pid, TOLERANCE_STEP) != "Completed"
    assert "Field Reopen" in [row["action_type"] for row in history(client, task["task_id"])]


# ---------------------------------------------------------------------------
# Card 4B -- the TWO tracked items of the consolidated Staking Letters page
# ---------------------------------------------------------------------------
# One page, one Save, TWO tracked outcomes: the Save PATCHes each owning task in
# turn and the engine decides each item from the fields that item owns.

STAKE_STEP = "Approval to Stake"
WELLSITE_STEP = "Well Site Location"

STAKE_OK = {"staking_well_created": "1", "approval_stake_letter_loaded": "1"}
WELLSITE_OK = {"wellsite_letter_loaded": "1", "staked_x": "532100.5", "staked_y": "2895120.1"}


def test_card_4b_registers_both_staking_letter_items(client):
    """Two items, and no field gates both -- otherwise one Save moves two dots."""
    import workflow

    stake = workflow.FIELD_COMPLETION[STAKE_STEP]
    wellsite = workflow.FIELD_COMPLETION[WELLSITE_STEP]
    assert stake == {"required_checked": ("staking_well_created", "approval_stake_letter_loaded")}
    assert wellsite == {"required_checked": ("wellsite_letter_loaded",),
                        "required_present": ("staked_x", "staked_y")}
    claimed = []
    for spec in (stake, wellsite):
        claimed.extend(spec.get("required_checked", ()))
        claimed.extend(spec.get("required_present", ()))
    assert len(claimed) == len(set(claimed)), "no field gates two different items"
    # Checkbox 1 is the v5 backfill, named once in the domain vocabulary.
    assert workflow.STAKING_WELL_CREATED_KEY == "staking_well_created"
    assert workflow.STAKING_WELL_CREATED_KEY in stake["required_checked"]
    # The retired step is NOT resurrected as a tracked item.
    assert "Well Creation" in workflow.RETIRED_TASK_NAMES
    assert "Well Creation" not in workflow.FIELD_COMPLETION


def test_the_stake_letter_alone_is_not_completion(client):
    """A filed letter for a well that does not exist is a document with no subject."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4B-LETTER-ONLY")
    task = save(client, get_task_by_name(client, pid, STAKE_STEP),
                {"approval_stake_letter_loaded": "1"})
    assert task["status"] != "Approved"
    assert tracked_item(client, pid, STAKE_STEP) != "Completed"


def test_well_creation_alone_is_not_completion_either(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4B-WELL-ONLY")
    task = save(client, get_task_by_name(client, pid, STAKE_STEP), {"staking_well_created": "1"})
    assert task["status"] != "Approved"


def test_both_boxes_complete_approval_to_stake_and_nothing_else(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4B-BOTH")
    task = save(client, get_task_by_name(client, pid, STAKE_STEP), STAKE_OK)
    assert task["status"] == "Approved"
    assert tracked_item(client, pid, STAKE_STEP) == "Completed"
    # ...and its page-mate is untouched by this save.
    assert tracked_item(client, pid, WELLSITE_STEP) != "Completed"


def test_a_v5_backfilled_lead_only_has_to_file_the_letter(client):
    """Migration v5 wrote staking_well_created='1' for every lead whose retired
    Well Creation step had been Approved. Such a lead opens the page with box
    one already ticked, so ticking box two ALONE completes the item.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4B-BACKFILL")
    task = get_task_by_name(client, pid, STAKE_STEP)
    # Stand in for the migration: a direct field write, no reconcile of its own.
    resp = client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields",
                        json={"fields": {"staking_well_created": "1"}})
    assert resp.status_code == 200, resp.get_json()
    task = get_task_by_name(client, pid, STAKE_STEP)
    assert task["status"] != "Approved", "the backfill alone completes nothing"

    task = save(client, task, {"approval_stake_letter_loaded": "1"})
    assert task["status"] == "Approved"
    assert tracked_item(client, pid, STAKE_STEP) == "Completed"


def test_the_wellsite_letter_needs_both_staked_coordinates(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4B-COORDS")
    task = get_task_by_name(client, pid, WELLSITE_STEP)

    task = save(client, task, {"wellsite_letter_loaded": "1"})
    assert task["status"] != "Approved", "a letter with no location leaves the site undefined"

    task = save(client, task, {"staked_x": "532100.5"})
    assert task["status"] != "Approved", "one coordinate is not a location"

    task = save(client, task, {"staked_y": "2895120.1"})
    assert task["status"] == "Approved"
    assert tracked_item(client, pid, WELLSITE_STEP) == "Completed"


def test_coordinates_without_the_letter_are_not_completion(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4B-NO-LETTER")
    task = save(client, get_task_by_name(client, pid, WELLSITE_STEP),
                {"staked_x": "532100.5", "staked_y": "2895120.1"})
    assert task["status"] != "Approved"


def test_a_zero_or_negative_staked_coordinate_is_still_a_coordinate(client):
    """NUMERIC_FIELDS, not POSITIVE_NUMBER_FIELDS -- see the 4A twin."""
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4B-ZERO")
    task = save(client, get_task_by_name(client, pid, WELLSITE_STEP),
                {"wellsite_letter_loaded": "1", "staked_x": "0", "staked_y": "-120"})
    assert task["status"] == "Approved"
    # But free text is not.
    task = save(client, task, {"staked_x": "TBD"})
    assert task["status"] == "In Progress"


def test_unticking_the_wellsite_letter_reopens_it_WITHOUT_clearing_the_survey(client):
    """THE PROMISE the page makes: hiding the fields never deletes the values.

    The consolidated page keeps the two inputs in the DOM when the box is
    unticked, so the save writes the SAME coordinates back beside the cleared
    confirmation. Only the item reopens; re-ticking finds the survey intact.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4B-KEEP")
    task = save(client, get_task_by_name(client, pid, WELLSITE_STEP), WELLSITE_OK)
    assert task["status"] == "Approved"

    # Exactly what the page's buildSavePlan sends after an untick.
    task = save(client, task, dict(WELLSITE_OK, wellsite_letter_loaded=""))
    assert task["status"] == "In Progress"
    fields = client.get(f"/api/tasks/{task['task_id']}/dynamic-fields").get_json()
    assert fields["staked_x"] == "532100.5"
    assert fields["staked_y"] == "2895120.1"

    # Re-ticking alone completes it again -- nothing had to be retyped.
    task = save(client, task, {"wellsite_letter_loaded": "1"})
    assert task["status"] == "Approved"


def test_one_page_press_produces_TWO_tracked_outcomes(client):
    """The end-to-end shape of one Save Updates press on the Staking Letters page.

    The page groups its five values by owning task and PATCHes each dirty one in
    turn -- two writes, two completed tracked items, one lead.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4B-PAGE")
    stake = save(client, get_task_by_name(client, pid, STAKE_STEP), STAKE_OK)
    wellsite = save(client, get_task_by_name(client, pid, WELLSITE_STEP), WELLSITE_OK)
    assert stake["status"] == "Approved"
    assert wellsite["status"] == "Approved"
    assert tracked_item(client, pid, STAKE_STEP) == "Completed"
    assert tracked_item(client, pid, WELLSITE_STEP) == "Completed"
    # Both items still keep their OWN comments column -- the page shows one
    # editable box and folds the other in read-only, it does not merge them.
    save(client, get_task_by_name(client, pid, STAKE_STEP), {}, comments="letter chased")
    tasks = {t["task_name"]: t for t in client.get(f"/api/projects/{pid}/tasks").get_json()}
    assert tasks[STAKE_STEP]["comments"] == "letter chased"
    assert (tasks[WELLSITE_STEP]["comments"] or "") == ""


# ---------------------------------------------------------------------------
# Card 4C -- Pre-Drilling GeoX Assessment completes from its stored results
# ---------------------------------------------------------------------------

def test_the_geox_assessment_is_field_driven_by_its_stored_mean(client):
    """The ASAS owner decision closed card 4C's open question.

    "Done" for externally-produced GeoX results is "the results are stored":
    the pre_drill mean is the one number downstream readers resolve the
    pre-drill volume from, the same single-key rule Resource Assessment uses.
    Without a predicate the step would be UNCOMPLETABLE -- the decision also
    removed its supervisor walk from the UI.
    """
    import workflow

    assert workflow.FIELD_COMPLETION["Pre-Drilling GeoX Assessment"] == {
        "required_present": ("pre_drill_piip_gas_mean",)}
    assert "Pre-Drilling GeoX Assessment" not in workflow.CHECKBOX_SUBMIT_STEPS
    assert "Pre-Drilling GeoX Assessment" in workflow.AUTO_APPROVE_ON_SAVE_STEPS
    assert "pre_drill_piip_gas_mean" in workflow.POSITIVE_NUMBER_FIELDS


def test_the_geox_assessment_auto_approves_when_its_mean_is_stored(client):
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4C-WALK")
    step = "Pre-Drilling GeoX Assessment"
    # The calculator write (the step's only input path) IS the completion.
    task = get_task_by_name(client, pid, step)
    client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields",
                 json={"fields": {"pre_drill_piip_gas_mean": "22.8"}})
    assert get_task_by_name(client, pid, step)["status"] == "Approved"
    assert tracked_item(client, pid, "GeoX Assessment") == "Completed"

    # Clearing the stored result reopens it (standard reconcile semantics);
    # a zero is not a volume either (POSITIVE_NUMBER_FIELDS).
    task = get_task_by_name(client, pid, step)
    client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields",
                 json={"fields": {"pre_drill_piip_gas_mean": ""}})
    assert get_task_by_name(client, pid, step)["status"] == "In Progress"


# ---------------------------------------------------------------------------
# The BP execution pipeline is OUTSIDE the auto-approve policy
# ---------------------------------------------------------------------------

def test_bp_steps_never_auto_approve_on_save_and_keep_the_manual_walk(client):
    """The owner decision names SEGMENT MATURATION steps only.

    Well Delivery / Post-Drilling / Post-Testing keep the supervisor's
    submit -> approve lifecycle: a field save on a BP step moves nothing, no
    matter how complete the data looks, and the manual walk still closes it.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-BP-POLICY", pipeline_type="bp",
                         business_plan_enabled=True, business_plan_year=2027)
    task = get_task_by_name(client, pid, "Quicklook Logs")
    saved = save(client, task, {"quicklook_pay_thickness_ft": "45",
                                "quicklook_average_porosity_pct": "8",
                                "quicklook_average_swt_pct": "35"})
    assert saved["status"] == "Not Assigned"
    assert not [row for row in history(client, task["task_id"])
                if row["action_type"] in ("Field Completion", "Field Reopen")]
    # A ticked sign-off pair on SAD Update is a submit GATE, not a completion.
    sad = get_task_by_name(client, pid, "SAD Update")
    saved = save(client, sad, {"sad_update_done": "1", "final_exec_summary_done": "1"})
    assert saved["status"] == "Not Assigned"
    # The manual walk is untouched.
    approved = drive_to_approved(client, pid, "Quicklook Logs")
    assert approved["status"] == "Approved"


def test_pre_well_delivery_four_of_four_matures_the_whole_lead(client):
    """The 12/12 walk: every tracked item Completed -> the lead is Completed.

    Cards 4A/4B close three of Pre-Well Delivery's four items from field state;
    the GeoX assessment closes from its stored mean. With Lead
    Assessment and Risk Analysis already done, that is 12 of 12 -- and a
    fully-matured lead LEAVES the prospect board and JOINS the Portfolio.
    """
    login(client, SUPERVISOR)
    pid = create_project(client, "FC-4-1212")

    # --- Lead Assessment (card 2B) ---------------------------------------
    _save_step(client, pid, AREA_STEP, AREA_OK)
    _save_step(client, pid, THICKNESS_STEP, THICKNESS_OK)
    _save_step(client, pid, GRV_STEP, GRV_OK)
    ra = get_task_by_name(client, pid, LEAD_STEP)
    client.patch(f"/api/tasks/{ra['task_id']}/dynamic-fields",
                 json={"fields": {"lead_piip_gas_mean": "19.4"}})
    _save_step(client, pid, RA_STEP, {"polygons_surfaces_loaded": "1"})
    # The save that completed the fourth checkpoint auto-approved the row
    # (ASAS owner decision) -- no manual walk left on this page.
    assert get_task_by_name(client, pid, LEAD_STEP)["status"] == "Approved"

    # --- Risk Analysis (cards 3A / 3B / 3C / 3D) --------------------------
    _save_step(client, pid, "Reservoir CoS",
               {"reservoir_cos_rows": json.dumps(RESERVOIR_ROWS), "reservoir_slides_loaded": "1"})
    _save_step(client, pid, "Trap and Seal CoS",
               dict(SEAL_INPUTS, sarah_quwarah_thickness_ft="120", seal_slides_loaded="1"))
    _save_step(client, pid, "Seismic Signature Validation", {"seismic_slides_loaded": "1"})
    # Card 3D keeps its human approval: the save submits, a supervisor approves.
    slides = _save_step(client, pid, "Segmentation Slides", {"segmentation_slides_loaded": "1"})
    assert slides["status"] == "Ready"
    _transition(client, get_task_by_name(client, pid, "Segmentation Slides"), "approve")

    # 8 of 12 so far -- the lead is not mature yet.
    assert client.get(f"/api/projects/{pid}").get_json()["overall_status"] != "Completed"

    # --- Pre-Well Delivery (cards 4A / 4B / 4C) ---------------------------
    _save_step(client, pid, TOLERANCE_STEP, TOLERANCE_OK)
    _save_step(client, pid, STAKE_STEP, STAKE_OK)
    _save_step(client, pid, WELLSITE_STEP, WELLSITE_OK)
    geox = get_task_by_name(client, pid, "Pre-Drilling GeoX Assessment")
    client.patch(f"/api/tasks/{geox['task_id']}/dynamic-fields",
                 json={"fields": {"pre_drill_piip_gas_mean": "17.2"}})

    # --- 12 of 12 ---------------------------------------------------------
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "Completed"
    assert len(project["tracked_items"]) == 12
    assert all(item["status"] == "Completed" for item in project["tracked_items"]), \
        project["tracked_items"]

    # The BOARD (and every KPI derived from it) excludes it outright: a matured
    # lead is no longer open prospect work, it is a proposal.
    board = client.get("/api/projects?pipeline_filter=prospect").get_json()
    assert pid not in [r["project_id"] for r in board]

    # ...and the Portfolio picks it up as a mature lead, STAKED (card 4B's
    # Approval to Stake is what reporting._approval_to_stake_map reads).
    resp = client.get("/api/portfolio/rows")
    assert resp.status_code == 200, resp.get_json()
    portfolio = resp.get_json()["rows"]
    entry = next(r for r in portfolio if r["project_id"] == pid)
    assert entry["is_lead"] == 1
    assert entry["status"] == "Staked"
