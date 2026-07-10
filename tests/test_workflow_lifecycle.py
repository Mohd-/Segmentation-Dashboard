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

def test_new_prospect_project_has_32_tasks_with_first_assigned(client):
    pid = create_project(client, "SEED-PROSPECT-1")
    tasks = get_tasks(client, pid)
    assert len(tasks) == 32

    first = tasks[0]
    assert first["task_name"] == "Reservoir Area Definition"
    assert first["status"] == "Assigned"
    assert first["planned_start"] is not None
    assert first["planned_finish"] is not None

    for task in tasks[1:]:
        assert task["status"] == "Not Assigned"
        assert task["planned_start"] is None
        assert task["planned_finish"] is None

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["current_task"] == "Reservoir Area Definition"


def test_new_bp_project_marks_prospect_stages_not_applicable(client):
    pid = create_project(
        client, "SEED-BP-1", pipeline_type="bp",
        business_plan_enabled=True, business_plan_year=2027,
    )
    tasks = get_tasks(client, pid)
    for task in tasks:
        if task["stage_group"] in PROSPECT_STAGES:
            assert task["status"] == "Not Applicable", task["task_name"]

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

def test_completion_percent_excludes_not_applicable_from_denominator(client):
    # A fresh BP project has all 13 Prospect-stage tasks marked Not Applicable,
    # leaving 19 applicable tasks (Well Delivery + Post-Drilling + Post-Testing).
    pid = create_project(
        client, "COMPLETION-BP-1", pipeline_type="bp",
        business_plan_enabled=True, business_plan_year=2029,
    )
    resp = client.get(f"/api/projects/{pid}/completion")
    assert resp.get_json() == {"percent": 0.0}

    tasks = get_tasks(client, pid)
    applicable = [t for t in tasks if t["status"] != "Not Applicable"]
    assert len(applicable) == 19

    for task in applicable[:2]:
        client.patch(f"/api/tasks/{task['task_id']}", json={
            "status": "Approved", "revision": task["revision"],
        })
    resp = client.get(f"/api/projects/{pid}/completion")
    assert resp.get_json() == {"percent": round(2 / 19 * 100, 1)}


def test_completion_percent_known_arithmetic_for_prospect(client):
    pid = create_project(client, "COMPLETION-PROSPECT-1")
    tasks = get_tasks(client, pid)
    assert len(tasks) == 32
    for task in tasks[:5]:
        client.patch(f"/api/tasks/{task['task_id']}", json={
            "status": "Approved", "revision": task["revision"],
        })
    resp = client.get(f"/api/projects/{pid}/completion")
    assert resp.get_json() == {"percent": round(5 / 32 * 100, 1)}


# ---------------------------------------------------------------------------
# overall_status -> Completed
# ---------------------------------------------------------------------------

def test_approving_all_prospect_tasks_completes_project(client):
    pid = create_project(client, "COMPLETE-ALL-1")
    tasks = get_tasks(client, pid)
    for task in tasks:
        if task["stage_group"] in PROSPECT_STAGES and task["status"] not in ("Approved", "Not Applicable"):
            resp = client.patch(f"/api/tasks/{task['task_id']}", json={
                "status": "Approved", "revision": task["revision"],
            })
            assert resp.status_code == 200, resp.get_json()

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "Completed"
    # Pinning actual (surprising) behavior: refresh_project_state() hardcodes the
    # "final" task/stage to the PDA component regardless of its own status once
    # overall_status flips to Completed for a prospect whose only approved work
    # was the Prospect-stage tasks. The BP-stage tasks (including PDA itself)
    # remain "Not Assigned" -- completion percent does NOT reach 100% here
    # because it counts across all is_active tasks, not just the tasks in the
    # pipeline's currently-applicable stages.
    assert project["current_task"] == "PDA"
    assert project["current_stage"] == "Post-Testing"

    completion = client.get(f"/api/projects/{pid}/completion").get_json()
    assert completion == {"percent": round(13 / 32 * 100, 1)}
