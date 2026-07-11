"""Tests for the Phase 4 deliberate behavior changes (v16 release).

Covers: centralized error handling (friendly duplicate-name message, generic
500 with no internal detail leakage), the projects.completed_at lifecycle, and
completion-month reporting bucketed by completed_at.
"""
from __future__ import annotations

from conftest import create_project, get_tasks

PROSPECT_STAGES = {"Lead Identification", "Risking", "Segmentation", "Pre-Well Delivery"}


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_duplicate_project_name_returns_friendly_message(client):
    create_project(client, "DUP-FRIENDLY-1")
    resp = client.post("/api/projects", json={"project_name": "DUP-FRIENDLY-1"})
    assert resp.status_code == 400
    assert resp.get_json()["detail"] == "A lead / well with this name already exists."


def test_non_numeric_business_plan_year_returns_year_message(client):
    resp = client.post("/api/projects", json={
        "project_name": "BADYEAR-TEXT-1",
        "business_plan_enabled": True,
        "business_plan_year": "not-a-year",
    })
    assert resp.status_code == 400
    assert resp.get_json()["detail"] == "Select a business plan year from 2026 to 2040."


def test_internal_error_returns_generic_500_without_leaking(client, monkeypatch):
    import main

    def boom(session, *args, **kwargs):
        raise Exception("SecretInternalDetail: connection pool exploded")

    monkeypatch.setattr(main.workflow, "get_projects", boom)
    resp = client.get("/api/projects")
    assert resp.status_code == 500
    assert resp.get_json() == {"detail": "Internal server error."}
    assert "SecretInternalDetail" not in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# completed_at lifecycle
# ---------------------------------------------------------------------------

def _approve_all_prospect_tasks(client, pid):
    for task in get_tasks(client, pid):
        if task["stage_group"] in PROSPECT_STAGES and task["status"] != "Approved":
            resp = client.patch(f"/api/tasks/{task['task_id']}", json={
                "status": "Approved", "revision": task["revision"],
            })
            assert resp.status_code == 200, resp.get_json()


def test_completed_at_set_on_completion_and_cleared_on_reopen(client):
    pid = create_project(client, "COMPLETED-AT-1")
    first_task_id = get_tasks(client, pid)[0]["task_id"]
    _approve_all_prospect_tasks(client, pid)

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "Completed"
    assert project["completed_at"]  # stamped at the completion transition

    # Reopen one component: the project returns to In Progress and the stamp clears.
    first = client.get(f"/api/tasks/{first_task_id}").get_json()
    resp = client.patch(f"/api/tasks/{first_task_id}", json={
        "status": "In Progress", "revision": first["revision"],
    })
    assert resp.status_code == 200
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] == "In Progress"
    assert project["completed_at"] is None


# ---------------------------------------------------------------------------
# Reporting functions kept for the Excel export (their HTTP routes were
# removed in the API trim; the v16 bug fixes stay covered at function level)
# ---------------------------------------------------------------------------

def test_dashboard_metrics_function_excludes_archived_projects(client):
    import db as dbmod
    import reporting

    pid = create_project(client, "METRICS-ARCHIVED-1")
    task = get_tasks(client, pid)[0]
    # v17 lifecycle: 'Ready' is the awaiting-approval state (the metric formerly
    # counted 'Under Review').
    resp = client.patch(f"/api/tasks/{task['task_id']}", json={
        "status": "Ready", "revision": task["revision"],
    })
    assert resp.status_code == 200

    session = dbmod.new_session()
    try:
        metrics, _stages, _owners = reporting.dashboard_metrics(session)
        assert metrics["Components Ready"] == 1
    finally:
        session.close()

    assert client.delete(f"/api/projects/{pid}").status_code == 200  # archives

    session = dbmod.new_session()
    try:
        metrics, _stages, _owners = reporting.dashboard_metrics(session)
        assert metrics["Components Ready"] == 0
    finally:
        session.close()


def test_monthly_completed_wells_function_buckets_by_completed_at(client):
    import db as dbmod
    import reporting
    from conftest import raw_sqlite_connect

    pid = create_project(client, "MONTHLY-BUCKET-1")
    _approve_all_prospect_tasks(client, pid)
    project = client.get(f"/api/projects/{pid}").get_json()
    completed_month = project["completed_at"][:7]

    # Simulate a much later edit: only last_updated moves; the completion
    # must stay bucketed in its completed_at month.
    conn = raw_sqlite_connect(client.db_path)
    conn.execute("UPDATE projects SET last_updated = '2030-12-31 00:00:00' WHERE project_id = ?", (pid,))
    conn.commit()
    conn.close()

    session = dbmod.new_session()
    try:
        monthly = reporting.monthly_progress_metrics(session, limit=12)
    finally:
        session.close()
    by_month = {row["month"]: row["wells_completed"] for row in monthly}
    assert by_month.get(completed_month) == 1
    assert by_month.get("2030-12") is None
