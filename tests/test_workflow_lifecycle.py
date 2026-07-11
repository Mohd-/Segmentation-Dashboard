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


def _deactivate_stage_tasks(db_path, project_id, stages):
    """Force the `final_done is None` fallback: mark every applicable-stage task
    inactive so refresh_project_state finds no rows to anchor on."""
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


def _refresh(app_modules, project_id):
    """Call workflow.refresh_project_state in its own committed transaction."""
    _, db = app_modules
    import workflow
    session = db.new_session()
    try:
        db.begin_write(session)
        workflow.refresh_project_state(session, project_id)
        session.commit()
    finally:
        session.close()


def test_prospect_completion_fallback_anchors_on_prospect_not_pda(client, app_modules):
    # Regression: the completed-with-no-active-rows fallback previously hardcoded
    # the BP-only literals "PDA"/"Post-Testing" regardless of pipeline_type,
    # mis-stamping prospect leads. It must derive the prospect anchors instead.
    pid = create_project(client, "FALLBACK-PROSPECT-1")
    _deactivate_stage_tasks(
        client.db_path, pid,
        ["Lead Identification", "Risking", "Segmentation", "Pre-Well Delivery"],
    )
    _refresh(app_modules, pid)

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "Completed"
    assert project["current_task"] == "Approval to Stake"
    assert project["current_stage"] == "Pre-Well Delivery"
    assert project["current_task"] != "PDA"
    assert project["current_stage"] != "Post-Testing"


def test_bp_completion_fallback_still_anchors_on_pda(client, app_modules):
    # The BP branch of the same fallback must keep anchoring on its own final
    # step, PDA / Post-Testing.
    pid = create_project(
        client, "FALLBACK-BP-1", pipeline_type="bp",
        business_plan_enabled=True, business_plan_year=2029,
    )
    _deactivate_stage_tasks(
        client.db_path, pid, ["Well Delivery", "Post-Drilling", "Post-Testing"],
    )
    _refresh(app_modules, pid)

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
