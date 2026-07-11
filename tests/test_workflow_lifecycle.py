"""Characterization tests for the task/project workflow lifecycle in database.py.

Pins: initial task seeding for prospect vs. bp pipelines, status-transition side
effects on actual_start/actual_finish, optimistic locking, completion-percent
arithmetic, and the overall_status transition to "Completed".
"""
from __future__ import annotations

from datetime import date

from conftest import create_project, get_task_by_name, get_tasks

PROSPECT_STAGES = {"Lead Identification", "Risking", "Segmentation", "Pre-Well Delivery"}


# ---------------------------------------------------------------------------
# Initial seeding
# ---------------------------------------------------------------------------

def test_new_prospect_project_has_31_tasks_all_not_assigned(client):
    # v17 lifecycle: every step (including the first) starts Not Assigned;
    # assignment is what moves a step to In Progress. current_task still
    # anchors on the first step. (31 tasks since v18 removed the Presence CoS
    # Evaluation step.)
    pid = create_project(client, "SEED-PROSPECT-1")
    tasks = get_tasks(client, pid)
    assert len(tasks) == 31

    first = tasks[0]
    assert first["task_name"] == "Reservoir Area Definition"
    assert first["status"] == "Not Assigned"

    for task in tasks[1:]:
        assert task["status"] == "Not Assigned"

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["current_task"] == "Reservoir Area Definition"


def test_new_bp_project_seeds_all_31_tasks_not_assigned(client):
    # Applicability is derived from pipeline_type, not stored per row: a BP
    # project still materializes all 31 tasks Not Assigned (the prospect-stage
    # rows simply fall outside its operating pipeline). current_task anchors on
    # the first BP step.
    pid = create_project(
        client, "SEED-BP-1", pipeline_type="bp",
        business_plan_enabled=True, business_plan_year=2027,
    )
    tasks = get_tasks(client, pid)
    assert len(tasks) == 31
    for task in tasks:
        assert task["status"] == "Not Assigned", task["task_name"]

    gate = get_task_by_name(client, pid, "BP Execution Gate")
    assert gate is not None
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["current_task"] == "BP Execution Gate"


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

def test_in_progress_sets_actual_start_to_today(client):
    pid = create_project(client, "TRANSITION-1")
    task = get_tasks(client, pid)[0]
    resp = client.patch(f"/api/tasks/{task['task_id']}", json={
        "status": "In Progress", "revision": task["revision"],
    })
    saved = resp.get_json()["task"]
    assert saved["actual_start"] == date.today().isoformat()


def test_approved_sets_actual_finish_and_backfills_actual_start(client):
    pid = create_project(client, "TRANSITION-2")
    task = get_tasks(client, pid)[0]
    # Task starts with no actual_start; go straight to Approved.
    assert task["actual_start"] is None
    resp = client.patch(f"/api/tasks/{task['task_id']}", json={
        "status": "Approved", "revision": task["revision"],
    })
    saved = resp.get_json()["task"]
    assert saved["actual_finish"] == date.today().isoformat()
    assert saved["actual_start"] == date.today().isoformat()


def test_moving_approved_back_to_in_progress_clears_actual_finish(client):
    pid = create_project(client, "TRANSITION-3")
    task = get_tasks(client, pid)[0]
    resp = client.patch(f"/api/tasks/{task['task_id']}", json={
        "status": "Approved", "revision": task["revision"],
    })
    saved = resp.get_json()["task"]
    assert saved["actual_finish"] is not None

    resp = client.patch(f"/api/tasks/{task['task_id']}", json={
        "status": "In Progress", "revision": saved["revision"],
    })
    saved2 = resp.get_json()["task"]
    assert saved2["actual_finish"] is None
    assert saved2["actual_start"] is not None  # actual_start survives


def test_not_assigned_also_clears_actual_start(client):
    pid = create_project(client, "TRANSITION-4")
    task = get_tasks(client, pid)[0]
    resp = client.patch(f"/api/tasks/{task['task_id']}", json={
        "status": "Approved", "revision": task["revision"],
    })
    saved = resp.get_json()["task"]

    resp = client.patch(f"/api/tasks/{task['task_id']}", json={
        "status": "Not Assigned", "revision": saved["revision"],
    })
    saved2 = resp.get_json()["task"]
    assert saved2["actual_start"] is None
    assert saved2["actual_finish"] is None


def test_approving_first_task_advances_current_task(client):
    pid = create_project(client, "ADVANCE-1")
    task = get_tasks(client, pid)[0]
    assert task["task_name"] == "Reservoir Area Definition"
    client.patch(f"/api/tasks/{task['task_id']}", json={
        "status": "Approved", "revision": task["revision"],
    })
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["current_task"] == "Thickness Estimation"


# ---------------------------------------------------------------------------
# Optimistic locking
# ---------------------------------------------------------------------------

def test_optimistic_locking_stale_revision_rejected(client):
    pid = create_project(client, "LOCKING-1")
    task_id = get_tasks(client, pid)[0]["task_id"]

    fetched = client.get(f"/api/tasks/{task_id}").get_json()
    revision = fetched["revision"]

    ok = client.patch(f"/api/tasks/{task_id}", json={"status": "In Progress", "revision": revision})
    assert ok.status_code == 200

    stale = client.patch(f"/api/tasks/{task_id}", json={"status": "Approved", "revision": revision})
    assert stale.status_code == 409


# ---------------------------------------------------------------------------
# Completion percent arithmetic
# ---------------------------------------------------------------------------

def test_completion_percent_scoped_to_bp_stages_for_bp_well(client):
    # Completion is scoped to the operating pipeline's stages, not stored
    # applicability: a BP well is measured against its 19 BP-stage tasks
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
    assert len(applicable) == 19

    for task in applicable[:2]:
        client.patch(f"/api/tasks/{task['task_id']}", json={
            "status": "Approved", "revision": task["revision"],
        })
    resp = client.get(f"/api/projects/{pid}/completion")
    assert resp.get_json() == {"percent": round(2 / 19 * 100, 1)}


def test_completion_percent_known_arithmetic_for_prospect(client):
    # Completion is scoped to the operating pipeline's stages: a prospect is
    # measured against its 12 Prospect-stage tasks only, not all 31 (the
    # BP-stage tasks belong to a pipeline it has not entered).
    pid = create_project(client, "COMPLETION-PROSPECT-1")
    tasks = get_tasks(client, pid)
    assert len(tasks) == 31
    prospect_tasks = [t for t in tasks if t["stage_group"] in PROSPECT_STAGES]
    assert len(prospect_tasks) == 12
    for task in prospect_tasks[:5]:
        client.patch(f"/api/tasks/{task['task_id']}", json={
            "status": "Approved", "revision": task["revision"],
        })
    resp = client.get(f"/api/projects/{pid}/completion")
    assert resp.get_json() == {"percent": round(5 / 12 * 100, 1)}


# ---------------------------------------------------------------------------
# overall_status -> Completed
# ---------------------------------------------------------------------------

def test_approving_all_prospect_tasks_completes_project(client):
    pid = create_project(client, "COMPLETE-ALL-1")
    tasks = get_tasks(client, pid)
    for task in tasks:
        if task["stage_group"] in PROSPECT_STAGES and task["status"] != "Approved":
            resp = client.patch(f"/api/tasks/{task['task_id']}", json={
                "status": "Approved", "revision": task["revision"],
            })
            assert resp.status_code == 200, resp.get_json()

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "Completed"
    # A completed project anchors on the final (highest-sequence) task of its
    # OWN pipeline: "Approval to Stake" / Pre-Well Delivery for a prospect
    # (a completed BP well anchors on PDA / Post-Testing), and completion
    # percent reads 100% because it is scoped to the pipeline's own stages.
    assert project["current_task"] == "Approval to Stake"
    assert project["current_stage"] == "Pre-Well Delivery"

    completion = client.get(f"/api/projects/{pid}/completion").get_json()
    assert completion == {"percent": 100.0}


def test_approving_all_bp_tasks_completes_bp_well_anchored_on_pda(client):
    # The other pipeline branch of the completion anchor: a BP well that
    # finishes every BP-stage task anchors on its own final task, PDA.
    pid = create_project(
        client, "COMPLETE-ALL-BP-1", pipeline_type="bp",
        business_plan_enabled=True, business_plan_year=2029,
    )
    for task in get_tasks(client, pid):
        if task["stage_group"] not in PROSPECT_STAGES and task["status"] != "Approved":
            resp = client.patch(f"/api/tasks/{task['task_id']}", json={
                "status": "Approved", "revision": task["revision"],
            })
            assert resp.status_code == 200, resp.get_json()

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "Completed"
    assert project["current_task"] == "PDA"
    assert project["current_stage"] == "Post-Testing"
    completion = client.get(f"/api/projects/{pid}/completion").get_json()
    assert completion == {"percent": 100.0}


# ---------------------------------------------------------------------------
# Derived board pointers (no stored current_stage/task/owner/overall_status)
# ---------------------------------------------------------------------------

def test_derived_pointers_track_first_open_task_of_half_approved_prospect(client):
    # Approve steps 1-3 (all of Lead Identification) and assign step 4: the
    # derived pointers must land on step 4 (Seismic Signature Validation /
    # Risking) and carry its assignee, on both the single-project read and the
    # board row.
    pid = create_project(client, "DERIVED-POINTERS-1")
    tasks = get_tasks(client, pid)
    for task in tasks[:3]:
        resp = client.patch(f"/api/tasks/{task['task_id']}", json={
            "status": "Approved", "revision": task["revision"],
        })
        assert resp.status_code == 200, resp.get_json()
    step4 = get_tasks(client, pid)[3]
    resp = client.post(f"/api/tasks/{step4['task_id']}/assign", json={
        "assignee": "Employee", "cascade": False, "revision": step4["revision"],
    })
    assert resp.status_code == 200, resp.get_json()

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "In Progress"
    assert project["current_task"] == "Seismic Signature Validation"
    assert project["current_stage"] == "Risking"
    assert project["current_owner"] == "Employee"

    row = next(r for r in client.get("/api/projects").get_json() if r["project_id"] == pid)
    assert row["current_task"] == "Seismic Signature Validation"
    assert row["current_stage"] == "Risking"
    assert row["current_owner"] == "Employee"
    assert row["overall_status"] == "In Progress"


def test_transition_approve_completes_and_reopen_clears_completed_at(client):
    # Approve steps 1-11 directly; walk the final prospect step (Approval to
    # Stake) through assign -> submit -> approve so transition_task performs
    # the completing write. Then reopen and confirm completed_at clears.
    pid = create_project(client, "DERIVED-COMPLETE-1")
    tasks = get_tasks(client, pid)
    prospect = [t for t in tasks if t["stage_group"] in PROSPECT_STAGES]
    for task in prospect[:-1]:
        resp = client.patch(f"/api/tasks/{task['task_id']}", json={
            "status": "Approved", "revision": task["revision"],
        })
        assert resp.status_code == 200, resp.get_json()
    last = get_tasks(client, pid)[prospect[-1]["sequence_no"] - 1]
    assert last["task_name"] == "Approval to Stake"
    assigned = client.post(f"/api/tasks/{last['task_id']}/assign", json={
        "assignee": "Employee", "cascade": False, "revision": last["revision"],
    }).get_json()["task"]
    ready = client.post(f"/api/tasks/{last['task_id']}/transition", json={
        "action": "submit", "revision": assigned["revision"],
    }).get_json()["task"]
    resp = client.post(f"/api/tasks/{last['task_id']}/transition", json={
        "action": "approve", "revision": ready["revision"],
    })
    assert resp.status_code == 200, resp.get_json()

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "Completed"
    assert project["completed_at"]  # stamped by the completing transition
    assert project["current_task"] == "Approval to Stake"
    assert project["current_stage"] == "Pre-Well Delivery"
    assert project["current_owner"] is None

    # Reopen: send the final step back to Ready (save path), then return it to
    # In Progress (transition path). The project reads In Progress again and
    # the completion stamp is gone.
    approved = client.get(f"/api/tasks/{last['task_id']}").get_json()
    resp = client.patch(f"/api/tasks/{last['task_id']}", json={
        "status": "Ready", "revision": approved["revision"],
    })
    assert resp.status_code == 200, resp.get_json()
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "In Progress"
    assert project["completed_at"] is None

    reready = client.get(f"/api/tasks/{last['task_id']}").get_json()
    resp = client.post(f"/api/tasks/{last['task_id']}/transition", json={
        "action": "return", "revision": reready["revision"],
    })
    assert resp.status_code == 200, resp.get_json()
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "In Progress"
    assert project["completed_at"] is None
    assert project["current_task"] == "Approval to Stake"


def test_owner_filter_matches_derived_owner(client):
    pid_a = create_project(client, "OWNER-FILTER-A")
    pid_b = create_project(client, "OWNER-FILTER-B")
    task_a = get_tasks(client, pid_a)[0]
    task_b = get_tasks(client, pid_b)[0]
    client.post(f"/api/tasks/{task_a['task_id']}/assign", json={
        "assignee": "Employee", "cascade": False, "revision": task_a["revision"],
    })
    client.post(f"/api/tasks/{task_b['task_id']}/assign", json={
        "assignee": "Staff Member", "cascade": False, "revision": task_b["revision"],
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
        ["Lead Identification", "Risking", "Segmentation", "Pre-Well Delivery"],
    )

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "Completed"
    assert project["current_task"] == "Approval to Stake"
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
        resp = client.patch(f"/api/tasks/{task['task_id']}", json={
            "status": "In Progress", "revision": task["revision"],
        })
        assert resp.status_code == 200, resp.get_json()

    rows = client.get(f"/api/activity?project_id={pid}").get_json()
    assert len(rows) >= 5  # creation event + one per status change
    keys = [(row["changed_at"], row["history_id"]) for row in rows]
    assert keys == sorted(keys, reverse=True)
