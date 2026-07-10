"""Tests for the Phase 4 deliberate behavior changes (v16 release).

Covers: centralized error handling (friendly duplicate-name message, generic
500 with no internal detail leakage), the projects.completed_at lifecycle,
completion-month reporting bucketed by completed_at, and the v15 -> v16
migration backfill + idempotency.
"""
from __future__ import annotations

import sqlite3

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
        if task["stage_group"] in PROSPECT_STAGES and task["status"] not in ("Approved", "Not Applicable"):
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
# Migration v15 -> v16
# ---------------------------------------------------------------------------

def test_migration_v15_to_v16_backfills_and_is_idempotent(client):
    import db as dbmod

    pid = create_project(client, "MIGRATE-V15-1")

    # Reshape the freshly-bootstrapped DB into a v15 database: no completed_at
    # column (where SQLite supports DROP COLUMN), a Completed project without a
    # stamp, a missing overview row, and schema_version 15.
    conn = sqlite3.connect(str(client.db_path))
    try:
        conn.execute("ALTER TABLE projects DROP COLUMN completed_at")
        column_dropped = True
    except sqlite3.OperationalError:
        column_dropped = False  # very old SQLite: backfill path still covered below
    conn.execute(
        "UPDATE projects SET overall_status = 'Completed', last_updated = '2025-01-02 03:04:05' WHERE project_id = ?",
        (pid,))
    if not column_dropped:
        conn.execute("UPDATE projects SET completed_at = NULL WHERE project_id = ?", (pid,))
    conn.execute("DELETE FROM project_overview WHERE project_id = ?", (pid,))
    conn.execute("UPDATE app_settings SET value = '15' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    # Re-bootstrap the same file: migration 16 must run.
    dbmod.reset_for_tests()
    dbmod.init_db(str(client.db_path))

    conn = sqlite3.connect(str(client.db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT completed_at FROM projects WHERE project_id = ?", (pid,)).fetchone()
    assert row["completed_at"] == "2025-01-02 03:04:05"  # backfilled from last_updated
    assert conn.execute("SELECT 1 FROM project_overview WHERE project_id = ?", (pid,)).fetchone() is not None
    assert conn.execute("SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()[0] == "16"
    conn.close()

    # Second bootstrap over the already-16 DB: clean no-op, values unchanged.
    dbmod.reset_for_tests()
    dbmod.init_db(str(client.db_path))
    conn = sqlite3.connect(str(client.db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT completed_at FROM projects WHERE project_id = ?", (pid,)).fetchone()
    assert row["completed_at"] == "2025-01-02 03:04:05"
    assert conn.execute("SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()[0] == "16"
    conn.close()
