"""Tests for the v17 status-model rework (WS4).

The 9-status dropdown is replaced by an implicit 4-state lifecycle:
Not Assigned -> In Progress (assignment) -> Ready (submit) -> Approved
(supervisor), with Return sending Ready back to In Progress. "Not Applicable"
remains internal-only (seeding/pipeline scoping) and is rejected at the API.

Covers: POST /assign target + cascade semantics, POST /transition happy paths
and wrong-state 400s, role gates (employee vs staff/supervisor), save_task's
optional status / preserved assignee, API rejection of legacy statuses, and
the v17 migration status collapse.
"""
from __future__ import annotations

import sqlite3

from conftest import create_project, get_tasks, raw_sqlite_connect

PROSPECT_STAGES = {"Lead Identification", "Risking", "Segmentation", "Pre-Well Delivery"}


def _assign(client, task_id, assignee, revision, cascade=False):
    resp = client.post(f"/api/tasks/{task_id}/assign", json={
        "assignee": assignee, "cascade": cascade, "revision": revision,
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["task"]


def _transition(client, task_id, action, revision):
    return client.post(f"/api/tasks/{task_id}/transition", json={
        "action": action, "revision": revision,
    })


def _login(client, name):
    resp = client.post("/api/login", json={"name": name})
    assert resp.status_code == 200, resp.get_json()


# ---------------------------------------------------------------------------
# Lifecycle happy paths (anonymous dev mode acts as supervisor)
# ---------------------------------------------------------------------------

def test_assign_moves_not_assigned_to_in_progress_and_stamps_start(client):
    pid = create_project(client, "LIFECYCLE-ASSIGN-1")
    task = get_tasks(client, pid)[0]
    assert task["status"] == "Not Assigned"

    after = _assign(client, task["task_id"], "Employee", task["revision"])
    assert after["status"] == "In Progress"
    assert after["assigned_to"] == "Employee"
    assert after["actual_start"] is not None

    # The project's current owner follows the assignment.
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["current_owner"] == "Employee"


def test_reassigning_an_in_progress_task_keeps_its_status(client):
    pid = create_project(client, "LIFECYCLE-REASSIGN-1")
    task = get_tasks(client, pid)[0]
    first = _assign(client, task["task_id"], "Employee", task["revision"])
    second = _assign(client, task["task_id"], "Staff Member", first["revision"])
    assert second["status"] == "In Progress"  # reassignment, not a state change
    assert second["assigned_to"] == "Staff Member"


def test_submit_approve_flow_advances_project(client):
    pid = create_project(client, "LIFECYCLE-FLOW-1")
    task = get_tasks(client, pid)[0]
    assigned = _assign(client, task["task_id"], "Employee", task["revision"])

    resp = _transition(client, task["task_id"], "submit", assigned["revision"])
    assert resp.status_code == 200
    ready = resp.get_json()["task"]
    assert ready["status"] == "Ready"
    assert ready["actual_finish"] is None

    resp = _transition(client, task["task_id"], "approve", ready["revision"])
    assert resp.status_code == 200
    approved = resp.get_json()["task"]
    assert approved["status"] == "Approved"
    assert approved["actual_finish"] is not None
    assert approved["actual_start"] is not None

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["current_task"] == "Thickness Estimation"


def test_return_sends_ready_back_to_in_progress(client):
    pid = create_project(client, "LIFECYCLE-RETURN-1")
    task = get_tasks(client, pid)[0]
    assigned = _assign(client, task["task_id"], "Employee", task["revision"])
    ready = _transition(client, task["task_id"], "submit", assigned["revision"]).get_json()["task"]

    resp = _transition(client, task["task_id"], "return", ready["revision"])
    assert resp.status_code == 200
    returned = resp.get_json()["task"]
    assert returned["status"] == "In Progress"
    assert returned["actual_finish"] is None
    assert returned["assigned_to"] == "Employee"  # return keeps the assignee


# ---------------------------------------------------------------------------
# Wrong-state and unknown-action validation
# ---------------------------------------------------------------------------

def test_submit_requires_in_progress(client):
    pid = create_project(client, "WRONGSTATE-1")
    task = get_tasks(client, pid)[0]  # Not Assigned
    resp = _transition(client, task["task_id"], "submit", task["revision"])
    assert resp.status_code == 400
    assert "Not Assigned" in resp.get_json()["detail"]


def test_approve_and_return_require_ready(client):
    pid = create_project(client, "WRONGSTATE-2")
    task = get_tasks(client, pid)[0]
    assigned = _assign(client, task["task_id"], "Employee", task["revision"])  # In Progress

    resp = _transition(client, task["task_id"], "approve", assigned["revision"])
    assert resp.status_code == 400
    resp = _transition(client, task["task_id"], "return", assigned["revision"])
    assert resp.status_code == 400


def test_unknown_transition_action_rejected(client):
    pid = create_project(client, "WRONGSTATE-3")
    task = get_tasks(client, pid)[0]
    resp = _transition(client, task["task_id"], "escalate", task["revision"])
    assert resp.status_code == 400
    assert "Unknown action" in resp.get_json()["detail"]


# ---------------------------------------------------------------------------
# Role gates
# ---------------------------------------------------------------------------

def test_employee_cannot_assign(client):
    pid = create_project(client, "ROLES-ASSIGN-1")
    task = get_tasks(client, pid)[0]
    _login(client, "Employee")
    resp = client.post(f"/api/tasks/{task['task_id']}/assign", json={
        "assignee": "Employee", "revision": task["revision"],
    })
    assert resp.status_code == 403


def test_employee_cannot_approve_or_return(client):
    pid = create_project(client, "ROLES-APPROVE-1")
    task = get_tasks(client, pid)[0]
    _login(client, "Supervisor")
    assigned = _assign(client, task["task_id"], "Employee", task["revision"])
    ready = _transition(client, task["task_id"], "submit", assigned["revision"]).get_json()["task"]

    _login(client, "Employee")
    resp = _transition(client, task["task_id"], "approve", ready["revision"])
    assert resp.status_code == 403
    resp = _transition(client, task["task_id"], "return", ready["revision"])
    assert resp.status_code == 403

    # Staff may not approve either -- supervisor only.
    _login(client, "Staff Member")
    resp = _transition(client, task["task_id"], "approve", ready["revision"])
    assert resp.status_code == 403

    _login(client, "Supervisor")
    resp = _transition(client, task["task_id"], "approve", ready["revision"])
    assert resp.status_code == 200


def test_employee_can_submit_own_task_but_not_someone_elses(client):
    pid = create_project(client, "ROLES-SUBMIT-1")
    tasks = get_tasks(client, pid)
    mine, theirs = tasks[0], tasks[1]

    _login(client, "Supervisor")
    mine_assigned = _assign(client, mine["task_id"], "Employee", mine["revision"])
    theirs_assigned = _assign(client, theirs["task_id"], "Staff Member", theirs["revision"])

    _login(client, "Employee")
    resp = _transition(client, theirs["task_id"], "submit", theirs_assigned["revision"])
    assert resp.status_code == 403
    assert "assigned to you" in resp.get_json()["detail"]

    resp = _transition(client, mine["task_id"], "submit", mine_assigned["revision"])
    assert resp.status_code == 200
    assert resp.get_json()["task"]["status"] == "Ready"


def test_staff_can_submit_a_task_assigned_to_someone_else(client):
    pid = create_project(client, "ROLES-SUBMIT-2")
    task = get_tasks(client, pid)[0]
    _login(client, "Supervisor")
    assigned = _assign(client, task["task_id"], "Employee", task["revision"])
    _login(client, "Staff Member")
    resp = _transition(client, task["task_id"], "submit", assigned["revision"])
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Cascade semantics
# ---------------------------------------------------------------------------

def test_assign_cascade_covers_subsequent_not_assigned_prospect_steps_only(client):
    pid = create_project(client, "CASCADE-1")
    tasks = get_tasks(client, pid)
    by_seq = {t["sequence_no"]: t for t in tasks}

    # Pre-existing work that the cascade must never touch:
    # step 5 Approved, step 6 In Progress and assigned to Employee.
    resp = client.patch(f"/api/tasks/{by_seq[5]['task_id']}", json={
        "status": "Approved", "revision": by_seq[5]["revision"],
    })
    assert resp.status_code == 200
    _assign(client, by_seq[6]["task_id"], "Employee", by_seq[6]["revision"])

    # Assign step 3 with cascade: steps 3..12 (the prospect pipeline since the
    # v18 renumbering) that are still Not Assigned all go In Progress with the
    # same assignee.
    _assign(client, by_seq[3]["task_id"], "Staff Member", by_seq[3]["revision"], cascade=True)

    after = {t["sequence_no"]: t for t in get_tasks(client, pid)}
    # Steps before the target are untouched.
    for seq in (1, 2):
        assert after[seq]["status"] == "Not Assigned"
        assert after[seq]["assigned_to"] is None
    # Target + subsequent Not Assigned prospect steps cascade.
    for seq in (3, 4, 7, 8, 9, 10, 11, 12):
        assert after[seq]["status"] == "In Progress", seq
        assert after[seq]["assigned_to"] == "Staff Member", seq
        assert after[seq]["revision"] == by_seq[seq]["revision"] + 1, seq
    # Pre-existing Approved / In Progress rows are never touched.
    assert after[5]["status"] == "Approved"
    assert after[5]["assigned_to"] is None
    assert after[6]["status"] == "In Progress"
    assert after[6]["assigned_to"] == "Employee"
    # BP-stage tasks are outside a prospect's applicable pipeline.
    for seq, task in after.items():
        if task["stage_group"] not in PROSPECT_STAGES:
            assert task["status"] == "Not Assigned", seq
            assert task["assigned_to"] is None, seq


def test_assign_without_cascade_touches_only_the_target(client):
    pid = create_project(client, "CASCADE-2")
    tasks = get_tasks(client, pid)
    _assign(client, tasks[2]["task_id"], "Employee", tasks[2]["revision"], cascade=False)
    after = get_tasks(client, pid)
    changed = [t for t in after if t["assigned_to"]]
    assert [t["task_id"] for t in changed] == [tasks[2]["task_id"]]


def test_assign_cascade_for_bp_project_stays_in_bp_stages(client):
    pid = create_project(
        client, "CASCADE-BP-1", pipeline_type="bp",
        business_plan_enabled=True, business_plan_year=2028,
    )
    tasks = get_tasks(client, pid)
    first_bp = next(t for t in tasks if t["status"] == "Not Assigned")
    _assign(client, first_bp["task_id"], "Employee", first_bp["revision"], cascade=True)
    after = get_tasks(client, pid)
    for task in after:
        if task["stage_group"] in PROSPECT_STAGES:
            assert task["status"] == "Not Applicable", task["task_name"]
        else:
            assert task["status"] == "In Progress", task["task_name"]
            assert task["assigned_to"] == "Employee"


# ---------------------------------------------------------------------------
# save_task: optional status, preserved assignee, rejected statuses
# ---------------------------------------------------------------------------

def test_save_without_status_keeps_current_status_and_assignee(client):
    pid = create_project(client, "SAVE-KEEP-1")
    task = get_tasks(client, pid)[0]
    assigned = _assign(client, task["task_id"], "Employee", task["revision"])

    resp = client.patch(f"/api/tasks/{task['task_id']}", json={
        "fields": {"p90_area_km2": "12"}, "revision": assigned["revision"],
    })
    assert resp.status_code == 200
    saved = resp.get_json()["task"]
    assert saved["status"] == "In Progress"       # NOT reset to Not Assigned
    assert saved["assigned_to"] == "Employee"     # NOT cleared


def test_save_rejects_not_applicable_and_legacy_statuses(client):
    pid = create_project(client, "SAVE-REJECT-1")
    task = get_tasks(client, pid)[0]
    for bad in ("Not Applicable", "Under Review", "Ready for Review",
                "Ready for Approval", "Returned for Update", "Assigned"):
        resp = client.patch(f"/api/tasks/{task['task_id']}", json={
            "status": bad, "revision": task["revision"],
        })
        assert resp.status_code == 400, bad
        assert resp.get_json()["detail"] == "Invalid component status."


# ---------------------------------------------------------------------------
# Migration v17: status collapse
# ---------------------------------------------------------------------------

def test_migration_v17_collapses_legacy_statuses(client):
    import db as dbmod

    pid = create_project(client, "MIGRATE-V17-1")
    tasks = get_tasks(client, pid)
    seq_to_id = {t["sequence_no"]: t["task_id"] for t in tasks}

    # (sequence_no, legacy status, assigned_to) -> expected status after v17.
    fixture = [
        (1, "Assigned", "Supervisor", "In Progress"),
        (2, "Assigned", None, "Not Assigned"),
        (3, "Ready for Review", "Employee", "Ready"),
        (4, "Under Review", "Employee", "Ready"),
        (5, "Ready for Approval", "Employee", "Ready"),
        (6, "Returned for Update", "Employee", "In Progress"),
        (7, "Approved", "Employee", "Approved"),
        (8, "In Progress", "Employee", "In Progress"),
        (9, "Not Assigned", None, "Not Assigned"),
        (10, "Not Applicable", None, "Not Applicable"),
    ]

    conn = raw_sqlite_connect(client.db_path)
    try:
        for seq, legacy, assignee, _expected in fixture:
            conn.execute(
                "UPDATE project_tasks SET status = ?, assigned_to = ? WHERE task_id = ?",
                (legacy, assignee, seq_to_id[seq]))
        # History rows carry no assignee context: 'Assigned' maps to
        # 'In Progress' unconditionally there.
        conn.execute("""
            INSERT INTO task_history (task_id, project_id, task_name, action_type,
                                      old_status, new_status, changed_at, changed_by, comment)
            VALUES (?, ?, 'Reservoir Area Definition', 'Component Update',
                    'Assigned', 'Under Review', datetime('now'), 'Tester', 'legacy row')
        """, (seq_to_id[1], pid))
        conn.execute("""
            INSERT INTO task_history (task_id, project_id, task_name, action_type,
                                      old_status, new_status, changed_at, changed_by, comment)
            VALUES (?, ?, 'Thickness Estimation', 'Component Update',
                    'Ready for Approval', 'Returned for Update', datetime('now'), 'Tester', 'legacy row')
        """, (seq_to_id[2], pid))
        conn.execute("UPDATE app_settings SET value = '16' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()

    # Re-bootstrap the same file: migration 17 must run.
    dbmod.reset_for_tests()
    dbmod.init_db(str(client.db_path))

    conn = sqlite3.connect(str(client.db_path))
    conn.row_factory = sqlite3.Row
    try:
        for seq, _legacy, _assignee, expected in fixture:
            row = conn.execute("SELECT status FROM project_tasks WHERE task_id = ?",
                               (seq_to_id[seq],)).fetchone()
            assert row["status"] == expected, f"seq {seq}"
        history = conn.execute("""
            SELECT old_status, new_status FROM task_history
            WHERE comment = 'legacy row' ORDER BY history_id
        """).fetchall()
        assert [(r["old_status"], r["new_status"]) for r in history] == [
            ("In Progress", "Ready"),        # Assigned -> In Progress; Under Review -> Ready
            ("Ready", "In Progress"),        # Ready for Approval -> Ready; Returned for Update -> In Progress
        ]
        version = conn.execute("SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()[0]
        assert int(version) >= 17
        statuses_after_first_run = [
            r["status"] for r in conn.execute(
                "SELECT status FROM project_tasks WHERE project_id = ? ORDER BY task_id", (pid,))
        ]
    finally:
        conn.close()

    # Idempotency: force the step to replay against already-collapsed data.
    conn = raw_sqlite_connect(client.db_path)
    conn.execute("UPDATE app_settings SET value = '16' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    dbmod.reset_for_tests()
    dbmod.init_db(str(client.db_path))

    conn = sqlite3.connect(str(client.db_path))
    conn.row_factory = sqlite3.Row
    try:
        statuses_after_second_run = [
            r["status"] for r in conn.execute(
                "SELECT status FROM project_tasks WHERE project_id = ? ORDER BY task_id", (pid,))
        ]
        assert statuses_after_second_run == statuses_after_first_run
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Migration v18: Presence CoS Evaluation step removal + 1-31 renumbering
# ---------------------------------------------------------------------------

def test_migration_v18_retires_presence_step_and_renumbers(client):
    import db as dbmod
    import migrations
    import workflow

    pid = create_project(client, "MIGRATE-V18-1")

    # Reshape the freshly-bootstrapped DB into a pre-v18 database: re-insert
    # the retired "Presence CoS Evaluation" step at its old slot (sequence 8,
    # Risking), shift every later step up by one (the old 1-32 numbering),
    # anchor the project on the doomed step, and stamp schema_version 17.
    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute("UPDATE task_templates SET sequence_no = sequence_no + 1 WHERE sequence_no >= 8")
        conn.execute("""
            INSERT INTO task_templates (template_id, sequence_no, task_name, stage_group,
                                        default_role, default_duration_days, branch_type, mandatory_output)
            VALUES (500, 8, 'Presence CoS Evaluation', 'Risking', 'Geologist', 2, 'normal', 'Presence CoS entered')
        """)
        conn.execute(
            "UPDATE project_tasks SET sequence_no = sequence_no + 1 WHERE project_id = ? AND sequence_no >= 8",
            (pid,))
        cursor = conn.execute("""
            INSERT INTO project_tasks (project_id, template_id, sequence_no, task_name, stage_group,
                                       status, priority, is_active, last_updated)
            VALUES (?, 500, 8, 'Presence CoS Evaluation', 'Risking', 'In Progress', 'Medium', 1, datetime('now'))
        """, (pid,))
        presence_task_id = cursor.lastrowid
        conn.execute("""
            INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
            VALUES (?, 'presence_cos', '42', datetime('now'))
        """, (presence_task_id,))
        conn.execute("UPDATE project_overview SET derisking = '42' WHERE project_id = ?", (pid,))
        conn.execute(
            "UPDATE projects SET current_task = 'Presence CoS Evaluation', current_stage = 'Risking' WHERE project_id = ?",
            (pid,))
        conn.execute("UPDATE app_settings SET value = '17' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()

    # Re-bootstrap the same file: migration 18 must run.
    dbmod.reset_for_tests()
    dbmod.init_db(str(client.db_path))

    conn = raw_sqlite_connect(client.db_path)
    try:
        # The retired step's row survives, deactivated; its data is untouched.
        presence = conn.execute(
            "SELECT is_active FROM project_tasks WHERE project_id = ? AND task_name = 'Presence CoS Evaluation'",
            (pid,)).fetchone()
        assert presence is not None
        assert presence["is_active"] == 0
        kept_field = conn.execute(
            "SELECT field_value FROM task_dynamic_fields WHERE task_id = ?",
            (presence_task_id,)).fetchone()
        assert kept_field["field_value"] == "42"

        # Active tasks renumbered to a contiguous 1-31 in the new order.
        rows = conn.execute("""
            SELECT task_name, sequence_no FROM project_tasks
            WHERE project_id = ? AND is_active = 1 ORDER BY sequence_no
        """, (pid,)).fetchall()
        assert [r["sequence_no"] for r in rows] == list(range(1, 32))
        assert [r["task_name"] for r in rows] == [t[1] for t in workflow.PIPELINE_TEMPLATES]

        # Templates: retired one parked at 999, the rest 1-31.
        assert conn.execute(
            "SELECT sequence_no FROM task_templates WHERE task_name = 'Presence CoS Evaluation'"
        ).fetchone()["sequence_no"] == 999
        template_seqs = [r["sequence_no"] for r in conn.execute(
            "SELECT sequence_no FROM task_templates WHERE task_name != 'Presence CoS Evaluation' ORDER BY sequence_no"
        ).fetchall()]
        assert template_seqs == list(range(1, 32))

        # Project state re-anchored off the removed step; derisking preserved.
        project = conn.execute(
            "SELECT current_task, current_stage FROM projects WHERE project_id = ?", (pid,)).fetchone()
        assert project["current_task"] == "Reservoir Area Definition"
        assert project["current_stage"] == "Lead Identification"
        assert conn.execute(
            "SELECT derisking FROM project_overview WHERE project_id = ?", (pid,)
        ).fetchone()["derisking"] == "42"
        assert conn.execute(
            "SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()[0] == str(migrations.LATEST_SCHEMA_VERSION)

        snapshot = conn.execute("""
            SELECT task_id, task_name, sequence_no, is_active, status FROM project_tasks
            WHERE project_id = ? ORDER BY task_id
        """, (pid,)).fetchall()
        snapshot_first = [tuple(r) for r in snapshot]
    finally:
        conn.close()

    # Idempotency: force the step to replay against already-migrated data.
    conn = raw_sqlite_connect(client.db_path)
    conn.execute("UPDATE app_settings SET value = '17' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    dbmod.reset_for_tests()
    dbmod.init_db(str(client.db_path))

    conn = raw_sqlite_connect(client.db_path)
    try:
        snapshot_second = [tuple(r) for r in conn.execute("""
            SELECT task_id, task_name, sequence_no, is_active, status FROM project_tasks
            WHERE project_id = ? ORDER BY task_id
        """, (pid,)).fetchall()]
        assert snapshot_second == snapshot_first
        assert conn.execute(
            "SELECT derisking FROM project_overview WHERE project_id = ?", (pid,)
        ).fetchone()["derisking"] == "42"
    finally:
        conn.close()


def test_new_project_on_migrated_db_skips_retired_templates(client):
    """A migrated DB keeps retired templates (parked at seq 999 for FK
    integrity); creating a NEW project must not spawn tasks from them.

    Regression: caught live on a v16->v19 migrated copy of the real database,
    where new leads were created with 32 tasks including the retired
    "Presence CoS Evaluation" step. Fresh-DB fixtures never see this because
    seed_templates only inserts the canonical 31.
    """
    import workflow

    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute("""
            INSERT INTO task_templates (sequence_no, task_name, stage_group,
                                        default_role, default_duration_days, branch_type, mandatory_output)
            VALUES (999, 'Presence CoS Evaluation', 'Risking', 'Geologist', 2, 'normal', 'retired')
        """)
        conn.commit()
    finally:
        conn.close()

    pid = create_project(client, "POST-MIGRATION-LEAD")
    tasks = get_tasks(client, pid)
    names = [t["task_name"] for t in tasks]
    assert "Presence CoS Evaluation" not in names
    assert len(tasks) == len(workflow.PIPELINE_TEMPLATES)
    assert sorted(t["sequence_no"] for t in tasks) == list(range(1, len(workflow.PIPELINE_TEMPLATES) + 1))
