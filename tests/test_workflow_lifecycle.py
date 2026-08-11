"""Characterization tests for the task/project workflow lifecycle in database.py.

Pins: initial task seeding for prospect vs. bp pipelines, status-transition side
effects on actual_start/actual_finish, optimistic locking, completion-percent
arithmetic, and the overall_status transition to "Completed".
"""
from __future__ import annotations

from datetime import date

from conftest import approve_task, create_project, get_task_by_name, get_tasks, reach_task

PROSPECT_STAGES = {"Lead Assessment", "Risk Analysis", "Pre-Well Delivery"}


# ---------------------------------------------------------------------------
# Initial seeding
# ---------------------------------------------------------------------------

def _fill_assessment_checkpoints(client, pid):
    task = get_task_by_name(client, pid, "Lead Assessment")
    resp = client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields", json={"fields": {
        "p90_area_km2": "1", "p10_area_km2": "2",
        "reservoir_thickness_ft": "1", "formation_thickness_ft": "2",
        "grv_p90_thousand_acre_ft": "1", "grv_p10_thousand_acre_ft": "2",
        "polygons_surfaces_loaded": "1", "lead_piip_gas_mean": "1",
    }})
    assert resp.status_code == 200, resp.get_json()


def test_new_prospect_project_has_24_tasks_and_none_are_auto_assigned(client):
    # v14: creation no longer auto-assigns steps. Every task seeds Not Assigned;
    # assignment only happens when a step is manually assigned or reaches its
    # turn in the pipeline. current_task still anchors on the first step.
    pid = create_project(client, "SEED-PROSPECT-1")
    tasks = get_tasks(client, pid)
    assert len(tasks) == 24

    first = tasks[0]
    assert first["task_name"] == "Lead Assessment"
    assert first["status"] == "Not Assigned"

    for task in tasks[1:]:
        assert task["status"] == "Not Assigned", task["task_name"]
        assert task["assigned_to"] is None, task["task_name"]

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["current_task"] == "Lead Assessment"


def test_new_bp_project_seeds_all_24_tasks_not_assigned(client):
    # Applicability is derived from pipeline_type, not stored per row: a BP
    # project still materializes all 24 tasks Not Assigned (the prospect-stage
    # rows simply fall outside its operating pipeline). current_task anchors on
    # the first BP step.
    pid = create_project(
        client, "SEED-BP-1", pipeline_type="bp",
        business_plan_enabled=True, business_plan_year=2027,
    )
    tasks = get_tasks(client, pid)
    assert len(tasks) == 24
    for task in tasks:
        assert task["status"] == "Not Assigned", task["task_name"]

    gate = get_task_by_name(client, pid, "BP Execution Gate")
    assert gate is not None
    bp_sequences = [task["sequence_no"] for task in tasks if task["stage_group"] not in PROSPECT_STAGES]
    assert bp_sequences == list(range(13, 28)), "v7 keeps the BP sequence contract stable"
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["current_task"] == "BP Execution Gate"


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

def test_in_progress_sets_actual_start_to_today(client):
    pid = create_project(client, "TRANSITION-1")
    task = get_tasks(client, pid)[0]
    resp = client.post(f"/api/tasks/{task['task_id']}/assign", json={
        "assigned_to": "Employee", "cascade": False, "revision": task["revision"],
    })
    saved = resp.get_json()["task"]
    assert saved["actual_start"] == date.today().isoformat()


def test_approved_sets_actual_finish_and_backfills_actual_start(client):
    pid = create_project(client, "TRANSITION-2")
    task = get_tasks(client, pid)[0]
    # Task starts with no actual_start; go straight to Approved.
    assert task["actual_start"] is None
    approve_task(client, task["task_id"])
    saved = client.get(f"/api/tasks/{task['task_id']}").get_json()
    assert saved["actual_finish"] == date.today().isoformat()
    assert saved["actual_start"] == date.today().isoformat()


def test_moving_approved_back_to_in_progress_clears_actual_finish(client):
    pid = create_project(client, "TRANSITION-3")
    task = get_tasks(client, pid)[0]
    approve_task(client, task["task_id"])
    saved = client.get(f"/api/tasks/{task['task_id']}").get_json()
    assert saved["actual_finish"] is not None

    # Auto-complete tasks have no public approval actions; internal lifecycle
    # automation can still reopen historical state.
    import db as dbmod
    import workflow
    session = dbmod.new_session()
    try:
        saved2 = workflow.transition_task(
            session, task["task_id"], "reopen",
            expected_revision=saved["revision"])
    finally:
        session.close()
    assert saved2["actual_finish"] is None
    assert saved2["actual_start"] is not None  # actual_start survives


def test_not_assigned_also_clears_actual_start(client):
    pid = create_project(client, "TRANSITION-4")
    task = get_tasks(client, pid)[0]
    approve_task(client, task["task_id"])
    saved = client.get(f"/api/tasks/{task['task_id']}").get_json()

    # Moving from Approved back to Not Assigned is not a public lifecycle
    # transition; exercise the domain save_task directly to pin the side effect
    # on actual_start / actual_finish.
    import db as dbmod
    import workflow
    session = dbmod.new_session()
    try:
        workflow.save_task(session, task["task_id"], {"status": "Not Assigned"})
    finally:
        session.close()
    saved2 = client.get(f"/api/tasks/{task['task_id']}").get_json()
    assert saved2["actual_start"] is None
    assert saved2["actual_finish"] is None


def test_approving_first_task_advances_current_task(client):
    pid = create_project(client, "ADVANCE-1")
    task = get_tasks(client, pid)[0]
    assert task["task_name"] == "Lead Assessment"
    approve_task(client, task["task_id"])
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["current_task"] == "Reservoir CoS"


# ---------------------------------------------------------------------------
# Optimistic locking
# ---------------------------------------------------------------------------

def test_optimistic_locking_stale_revision_rejected(client):
    pid = create_project(client, "LOCKING-1")
    task_id = get_tasks(client, pid)[0]["task_id"]

    fetched = client.get(f"/api/tasks/{task_id}").get_json()
    revision = fetched["revision"]

    assigned = client.post(f"/api/tasks/{task_id}/assign", json={
        "assigned_to": "Employee", "cascade": False, "revision": revision,
    }).get_json()["task"]

    stale = client.post(f"/api/tasks/{task_id}/transition", json={
        "action": "submit", "revision": revision,
    })
    assert stale.status_code == 409


# ---------------------------------------------------------------------------
# Completion percent arithmetic
# ---------------------------------------------------------------------------

def test_completion_percent_scoped_to_bp_stages_for_bp_well(client):
    # Completion is scoped to the operating pipeline's stages, not stored
    # applicability: a BP well is measured against its 15 BP-stage tasks
    # (Well Delivery + Post-Drilling + Post-Testing), never the 12 prospect
    # rows that fall outside its pipeline.
    pid = create_project(
        client, "COMPLETION-BP-1", pipeline_type="bp",
        business_plan_enabled=True, business_plan_year=2029,
    )
    resp = client.get(f"/api/projects/{pid}/completion")
    assert resp.get_json() == {"percent": 0.0}

    tasks = get_tasks(client, pid)
    applicable = [t for t in tasks if t["stage_group"] not in PROSPECT_STAGES]
    assert len(applicable) == 15

    for task in applicable[:2]:
        approve_task(client, task["task_id"])
    resp = client.get(f"/api/projects/{pid}/completion")
    assert resp.get_json() == {"percent": round(2 / 15 * 100, 1)}


def test_completion_percent_known_arithmetic_for_prospect(client):
    # Completion is scoped to the operating pipeline's stages: a prospect is
    # measured against 12 communicated items: four field checkpoints and eight
    # ordinary task rows, despite having only nine prospect lifecycle rows.
    pid = create_project(client, "COMPLETION-PROSPECT-1")
    tasks = get_tasks(client, pid)
    assert len(tasks) == 24
    prospect_tasks = [t for t in tasks if t["stage_group"] in PROSPECT_STAGES]
    assert len(prospect_tasks) == 9
    _fill_assessment_checkpoints(client, pid)  # four completed items
    for task in prospect_tasks[1:2]:           # plus Reservoir CoS
        approve_task(client, task["task_id"])
    resp = client.get(f"/api/projects/{pid}/completion")
    assert resp.get_json() == {"percent": round(5 / 12 * 100, 1)}


# ---------------------------------------------------------------------------
# overall_status -> Completed
# ---------------------------------------------------------------------------

def test_approving_all_prospect_tasks_completes_project(client):
    pid = create_project(client, "COMPLETE-ALL-1")
    _fill_assessment_checkpoints(client, pid)
    tasks = get_tasks(client, pid)
    for task in tasks:
        if task["stage_group"] not in PROSPECT_STAGES:
            continue
        # Refetch: the staking-maturation hook approves steps 8/9 the moment
        # step 7 ("Approval to Stake") approves, so the pre-loop snapshot goes
        # stale mid-walk; skip rows the hook already closed.
        task = client.get(f"/api/tasks/{task['task_id']}").get_json()
        if task["status"] != "Approved":
            approve_task(client, task["task_id"])
            task = client.get(f"/api/tasks/{task['task_id']}").get_json()
            assert task["status"] == "Approved"

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "Completed"
    # A completed project anchors on the final (highest-sequence) task of its
    # OWN pipeline: "Pre-Drilling GeoX Assessment" / Pre-Well Delivery for a
    # prospect (a completed BP well anchors on PDA / Post-Testing), and
    # completion percent reads 100% because it is scoped to the pipeline's own
    # stages.
    assert project["current_task"] == "Pre-Drilling GeoX Assessment"
    assert project["current_stage"] == "Pre-Well Delivery"

    completion = client.get(f"/api/projects/{pid}/completion").get_json()
    assert completion == {"percent": 100.0}


def test_approving_stake_step_auto_matures_prospect(client):
    # Staking maturation: approving "Approval to Stake" (step 7) IS the prospect's exit
    # decision. The maturation hook must close the two trailing Pre-Well
    # Delivery steps on its own -- nobody touches steps 8/9 here -- so the
    # record derives Completed and leaves the maturation board.
    pid = create_project(client, "STAKE-MATURE-1")
    stake = reach_task(client, pid, "Approval to Stake")
    assert get_task_by_name(client, pid, "Well Site Location")["status"] == "Not Assigned"
    assert get_task_by_name(client, pid, "Pre-Drilling GeoX Assessment")["status"] == "Not Assigned"

    approve_task(client, stake["task_id"])

    assert get_task_by_name(client, pid, "Well Site Location")["status"] == "Approved"
    assert get_task_by_name(client, pid, "Pre-Drilling GeoX Assessment")["status"] == "Approved"

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "Completed"
    assert project["completed_at"]  # stamped by the hook's completing transition

    # The maturation leaves an audit trail: one "Staking Maturation" event per
    # step it closed, alongside the ordinary transition events of the walk.
    rows = client.get(f"/api/activity?project_id={pid}").get_json()
    matured = {row["task_name"] for row in rows
               if row["action_type"] == "Staking Maturation"}
    assert matured == {"Well Site Location", "Pre-Drilling GeoX Assessment"}


def test_reopening_stake_step_does_not_reverse_the_maturation(client):
    # The maturation walk is deliberately one-way: reopening "Approval to
    # Stake" questions the staking decision, not the site work recorded after
    # it, so steps 8/9 keep their Approved status and the record re-anchors on
    # the stake step (first open task) as an ordinary In Progress lead.
    import db as dbmod
    import workflow
    pid = create_project(client, "STAKE-REOPEN-1")
    stake = reach_task(client, pid, "Approval to Stake")
    approve_task(client, stake["task_id"])
    assert client.get(f"/api/projects/{pid}").get_json()["overall_status"] == "Completed"

    approved = client.get(f"/api/tasks/{stake['task_id']}").get_json()
    session = dbmod.new_session()
    try:
        workflow.transition_task(
            session, stake["task_id"], "reopen",
            expected_revision=approved["revision"])
    finally:
        session.close()

    assert get_task_by_name(client, pid, "Well Site Location")["status"] == "Approved"
    assert get_task_by_name(client, pid, "Pre-Drilling GeoX Assessment")["status"] == "Approved"
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "In Progress"
    assert project["completed_at"] is None
    assert project["current_task"] == "Approval to Stake"


def test_approving_all_bp_tasks_completes_bp_well_anchored_on_pda(client):
    # The other pipeline branch of the completion anchor: a BP well that
    # finishes every BP-stage task anchors on its own final task, PDA.
    pid = create_project(
        client, "COMPLETE-ALL-BP-1", pipeline_type="bp",
        business_plan_enabled=True, business_plan_year=2029,
    )
    for task in get_tasks(client, pid):
        if task["stage_group"] not in PROSPECT_STAGES and task["status"] != "Approved":
            approve_task(client, task["task_id"])
            task = client.get(f"/api/tasks/{task['task_id']}").get_json()
            assert task["status"] == "Approved"

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "Completed"
    assert project["current_task"] == "PDA"
    assert project["current_stage"] == "Post-Testing"
    completion = client.get(f"/api/projects/{pid}/completion").get_json()
    assert completion == {"percent": 100.0}


# ---------------------------------------------------------------------------
# Derived board pointers (no stored current_stage/task/owner/overall_status)
# ---------------------------------------------------------------------------

def test_derived_pointers_track_first_open_task_after_assessment(client):
    # Approve the one Lead Assessment lifecycle and assign Reservoir CoS: the derived
    # pointers must land on Reservoir CoS / Risk Analysis and carry its
    # assignee, on both the single-project read and the board row.
    pid = create_project(client, "DERIVED-POINTERS-1")
    tasks = get_tasks(client, pid)
    approve_task(client, tasks[0]["task_id"])
    reservoir = get_tasks(client, pid)[1]
    resp = client.post(f"/api/tasks/{reservoir['task_id']}/assign", json={
        "assigned_to": "Employee", "cascade": False, "revision": reservoir["revision"],
    })
    assert resp.status_code == 200, resp.get_json()

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "In Progress"
    assert project["current_task"] == "Reservoir CoS"
    assert project["current_stage"] == "Risk Analysis"
    assert project["current_owner"] == "Employee"

    row = next(r for r in client.get("/api/projects").get_json() if r["project_id"] == pid)
    assert row["current_task"] == "Reservoir CoS"
    assert row["current_stage"] == "Risk Analysis"
    assert row["current_owner"] == "Employee"
    assert row["overall_status"] == "In Progress"


# ---------------------------------------------------------------------------
# Submit gating (workflow.constants.REQUIRED_FIELDS_FOR_SUBMIT)
# ---------------------------------------------------------------------------

def _assign_and_ready(client, pid, task_name):
    """Assign a step to Employee so it sits In Progress; return the fresh row."""
    task = reach_task(client, pid, task_name)
    return client.post(f"/api/tasks/{task['task_id']}/assign", json={
        "assigned_to": "Employee", "cascade": False, "revision": task["revision"],
    }).get_json()["task"]


def _submit(client, task):
    return client.post(f"/api/tasks/{task['task_id']}/transition",
                       json={"action": "submit", "revision": task["revision"]})


def _bp_project(client, name):
    return create_project(client, name, pipeline_type="bp",
                          business_plan_enabled=True, business_plan_year=2029)


def test_submit_is_blocked_until_segmentation_slides_checkbox_is_checked(client):
    """The Segment approval cannot submit before its draft checkbox is saved."""
    pid = create_project(client, "GATE-SLIDES-1")
    task = _assign_and_ready(client, pid, "Segmentation Slides")

    resp = _submit(client, task)
    assert resp.status_code == 400, resp.get_json()
    message = resp.get_json()["detail"]
    assert "Segmentation slides" in message
    # Refused, not half-applied.
    assert get_task_by_name(client, pid, "Segmentation Slides")["status"] == "In Progress"

    client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields",
                 json={"fields": {"segmentation_slides_loaded": "1"}})
    task = get_task_by_name(client, pid, "Segmentation Slides")
    resp = _submit(client, task)
    assert resp.status_code == 200, resp.get_json()
    assert get_task_by_name(client, pid, "Segmentation Slides")["status"] == "Ready"


def test_submit_gate_rejects_a_falsy_checkbox_value(client):
    """An unticked checkbox saves as '' (or '0'), which must not pass the gate
    -- truthiness matches the app's '1'/'true'/'yes'/'on' vocabulary."""
    pid = create_project(client, "GATE-SLIDES-2")
    task = _assign_and_ready(client, pid, "Segmentation Slides")
    client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields",
                 json={"fields": {"segmentation_slides_loaded": "0"}})
    task = get_task_by_name(client, pid, "Segmentation Slides")
    assert _submit(client, task).status_code == 400

    client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields",
                 json={"fields": {"segmentation_slides_loaded": "yes"}})
    task = get_task_by_name(client, pid, "Segmentation Slides")
    assert _submit(client, task).status_code == 200


def test_auto_complete_steps_expose_no_public_submit(client):
    pid = _bp_project(client, "GATE-SAD-3")
    task = _assign_and_ready(client, pid, "Flowback Results")
    resp = _submit(client, task)
    assert resp.status_code == 400
    assert "completes automatically" in resp.get_json()["detail"]
    assert get_task_by_name(client, pid, "Flowback Results")["status"] == "In Progress"


def test_transition_approve_completes_and_reopen_clears_completed_at(client):
    # Approve the prospect steps programmatically. The staking-maturation
    # hook closes the final two steps the moment "Approval to Stake" approves,
    # so the completing write is the hook's inner GeoX approve -- still a real
    # transition_task walk. Then reopen the final step and confirm
    # completed_at clears.
    pid = create_project(client, "DERIVED-COMPLETE-1")
    tasks = get_tasks(client, pid)
    prospect = [t for t in tasks if t["stage_group"] in PROSPECT_STAGES]
    for task in prospect[:-1]:
        # Refetch: the hook approves step 8 mid-loop when step 7 approves.
        current = client.get(f"/api/tasks/{task['task_id']}").get_json()
        if current["status"] != "Approved":
            approve_task(client, task["task_id"])
    last = get_tasks(client, pid)[prospect[-1]["sequence_no"] - 1]
    assert last["task_name"] == "Pre-Drilling GeoX Assessment"
    assert last["status"] == "Approved"  # closed by the maturation hook

    import db as dbmod
    import workflow
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "Completed"
    assert project["completed_at"]  # stamped by the completing transition
    assert project["current_task"] == "Pre-Drilling GeoX Assessment"
    assert project["current_stage"] == "Pre-Well Delivery"
    assert project["current_owner"] is None

    # Internal automation may reopen an auto-complete task; the public endpoint
    # deliberately does not expose approval actions for it.
    approved = client.get(f"/api/tasks/{last['task_id']}").get_json()
    session = dbmod.new_session()
    try:
        workflow.transition_task(
            session, last["task_id"], "reopen",
            expected_revision=approved["revision"])
    finally:
        session.close()
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "In Progress"
    assert project["completed_at"] is None
    assert project["current_task"] == "Pre-Drilling GeoX Assessment"


def test_owner_filter_matches_derived_owner(client):
    pid_a = create_project(client, "OWNER-FILTER-A")
    pid_b = create_project(client, "OWNER-FILTER-B")
    task_a = get_tasks(client, pid_a)[0]
    task_b = get_tasks(client, pid_b)[0]
    client.post(f"/api/tasks/{task_a['task_id']}/assign", json={
        "assigned_to": "Employee", "cascade": False, "revision": task_a["revision"],
    })
    client.post(f"/api/tasks/{task_b['task_id']}/assign", json={
        "assigned_to": "Staff Member", "cascade": False, "revision": task_b["revision"],
    })

    rows = client.get("/api/projects?owner_filter=Employee").get_json()
    assert [r["project_id"] for r in rows] == [pid_a]
    rows = client.get("/api/projects?owner_filter=Staff Member").get_json()
    assert [r["project_id"] for r in rows] == [pid_b]


def _deactivate_stage_tasks(db_path, project_id, stages):
    """Force the no-active-rows template fallback: mark every applicable-stage
    task inactive so the derived state has no task rows to anchor on."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        placeholders = ",".join("?" for _ in stages)
        conn.execute(
            f"UPDATE project_tasks SET is_active = 0 "
            f"WHERE project_id = ? AND stage_group IN ({placeholders})",
            [project_id, *stages],
        )
        conn.commit()
    finally:
        conn.close()


def test_prospect_completion_fallback_anchors_on_prospect_not_pda(client):
    # Regression: the completed-with-no-active-rows fallback previously hardcoded
    # the BP-only literals "PDA"/"Post-Testing" regardless of pipeline_type,
    # mis-stamping prospect leads. The derived anchor must come from the
    # prospect templates instead -- and being derived at read time, it needs no
    # refresh call after the raw data change.
    pid = create_project(client, "FALLBACK-PROSPECT-1")
    _deactivate_stage_tasks(
        client.db_path, pid,
        ["Lead Assessment", "Risk Analysis", "Pre-Well Delivery"],
    )

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "Completed"
    assert project["current_task"] == "Pre-Drilling GeoX Assessment"
    assert project["current_stage"] == "Pre-Well Delivery"
    assert project["current_task"] != "PDA"
    assert project["current_stage"] != "Post-Testing"


def test_bp_completion_fallback_still_anchors_on_pda(client):
    # The BP branch of the same fallback must keep anchoring on its own final
    # step, PDA / Post-Testing.
    pid = create_project(
        client, "FALLBACK-BP-1", pipeline_type="bp",
        business_plan_enabled=True, business_plan_year=2029,
    )
    _deactivate_stage_tasks(
        client.db_path, pid, ["Well Delivery", "Post-Drilling", "Post-Testing"],
    )

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "Completed"
    assert project["current_task"] == "PDA"
    assert project["current_stage"] == "Post-Testing"


def test_activity_log_order_is_deterministic_within_same_second(client):
    # Several history rows are written within the same changed_at second here;
    # history_id must break the tie so the log reads newest-insert-first.
    pid = create_project(client, "ACTIVITY-ORDER-1")
    tasks = get_tasks(client, pid)
    for task in tasks[:4]:
        resp = client.post(f"/api/tasks/{task['task_id']}/assign", json={
            "assigned_to": "Employee", "cascade": False, "revision": task["revision"],
        })
        assert resp.status_code == 200, resp.get_json()

    rows = client.get(f"/api/activity?project_id={pid}").get_json()
    assert len(rows) >= 5  # creation event + one per status change
    keys = [(row["changed_at"], row["history_id"]) for row in rows]
    assert keys == sorted(keys, reverse=True)
