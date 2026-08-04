"""Former known-bug tests, fixed in the v16 release (Phase 4).

These originally pinned the DESIRED behavior with xfail(strict=False) while the
bugs still existed. All three bugs are now fixed (archived-task dashboard
metric, hardcoded health version, duplicate rows from the quicklook join), so
they run as ordinary tests.
"""
from __future__ import annotations

import sqlite3

from conftest import create_project, get_task_by_name, get_tasks


def test_health_version_reflects_real_schema_version(client):
    resp = client.get("/api/health")
    assert resp.get_json()["version"] != "v12"


def test_get_projects_no_duplicate_row_for_legacy_quicklook_task(client):
    pid = create_project(client, "BUG-DUP-QUICKLOOK-1")
    interpretation_task = get_task_by_name(client, pid, "Quicklook Logs")
    client.patch(f"/api/tasks/{interpretation_task['task_id']}/dynamic-fields", json={
        "fields": {"active_drilling": "yes"},
    })

    # Simulate a leftover legacy task row still named "Quicklook Logs
    # Interpretation" -- the pre-v3 name, which the v3 rename deliberately
    # SKIPS for any project that already holds both names -- that also carries
    # an active_drilling dynamic field. workflow.projects' board query reads
    # both names (belt and braces), so this is exactly the shape that used to
    # duplicate the project row. The application itself cannot create it today;
    # we insert it directly via sqlite3.
    conn = sqlite3.connect(str(client.db_path))
    conn.row_factory = sqlite3.Row
    try:
        source_row = conn.execute(
            "SELECT sequence_no, stage_group FROM project_tasks WHERE task_id = ?",
            (interpretation_task["task_id"],),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO project_tasks
                (project_id, sequence_no, task_name, stage_group,
                 status, priority, is_active, last_updated)
            VALUES (?, ?, 'Quicklook Logs Interpretation', ?, 'Not Assigned', 'Medium', 1, datetime('now'))
            """,
            (pid, source_row["sequence_no"], source_row["stage_group"]),
        )
        new_task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
            VALUES (?, 'active_drilling', 'yes', datetime('now'))
            """,
            (new_task_id,),
        )
        conn.commit()
    finally:
        conn.close()

    rows = client.get("/api/projects").get_json()
    occurrences = [row for row in rows if row["project_id"] == pid]
    assert len(occurrences) == 1
