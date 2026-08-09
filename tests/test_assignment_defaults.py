"""Domain role based assignment (v14).

Steps no longer auto-assign at creation.  Instead, when a step becomes the next
unapproved step in its active pipeline, the domain role mapped to that step
(if any) is consulted and every active member is assigned.

Tests cover: role/membership/mapping management, the sync script, activation
semantics, manual preassignment, multi-member assignment, snapshot behaviour
(role changes only affect tasks reached afterwards), and board visibility.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

import pytest

from conftest import approve_task, create_project, get_task_by_name, get_tasks, raw_sqlite_connect, reach_task

PRE_WELL_STEPS = ["Moving Tolerance", "Approval to Stake",
                  "Well Site Location", "Pre-Drilling GeoX Assessment"]


def _session(app_modules):
    _main, dbmod = app_modules
    return dbmod.new_session()


def _setup_role(app_modules, role_name: str, members: List[str],
                mappings: Dict[str, str]) -> Dict[str, Any]:
    """Create a role, add members, and map task names to it.

    Returns the sync-style manifest and summary for callers that need it.
    """
    import scripts.sync_domain_assignments as sync

    manifest = {
        "roles": [{"name": role_name}],
        "memberships": [{"user": m, "role": role_name} for m in members],
        "mappings": [{"task": task, "role": role_name} for task in mappings],
    }
    session = _session(app_modules)
    try:
        sync._validate_manifest(session, manifest)
        summary = sync._sync(session, manifest)
    finally:
        session.close()
    return manifest, summary


def _activate(session, task_id: int, actor: str = "Supervisor"):
    """Call the domain activation helper for a single task."""
    import workflow
    return workflow.lifecycle.activate_task(session, task_id, actor)


def _activate_next(session, project_id: int, actor: str = "Supervisor"):
    """Trigger activation for the first Not Assigned task of a project."""
    import workflow
    return workflow.lifecycle.activate_next_task(session, project_id, actor)


def _row(client, pid):
    rows = client.get("/api/projects?pipeline_filter=prospect").get_json()
    matches = [r for r in rows if r["project_id"] == pid]
    assert matches
    return matches[0]


# ---------------------------------------------------------------------------
# Sync script
# ---------------------------------------------------------------------------

def test_sync_domain_assignments_script_creates_roles_memberships_and_mappings(
        client, app_modules, tmp_path):
    import scripts.sync_domain_assignments as sync
    import workflow

    manifest = {
        "roles": [{"name": "Geophysicist"}, {"name": "Petrophysicist"}],
        "memberships": [
            {"user": "Employee", "role": "Geophysicist"},
            {"user": "Staff Member", "role": "Geophysicist"},
            {"user": "Supervisor", "role": "Petrophysicist"},
        ],
        "mappings": [
            {"task": "Seismic Signature Validation", "role": "Geophysicist"},
            {"task": "Reservoir CoS", "role": "Petrophysicist"},
        ],
    }

    session = _session(app_modules)
    try:
        sync._validate_manifest(session, manifest)
        summary = sync._sync(session, manifest)
    finally:
        session.close()

    # Petrophysicist is a bootstrap default; the exact manifest adds only the
    # new Geophysicist role and removes the unrelated defaults.
    assert summary["created_roles"] == ["Geophysicist"]
    assert sorted(summary["added_memberships"]) == [
        "Employee -> Geophysicist",
        "Staff Member -> Geophysicist",
        "Supervisor -> Petrophysicist",
    ]
    assert sorted(summary["set_mappings"]) == [
        "Reservoir CoS -> Petrophysicist",
        "Seismic Signature Validation -> Geophysicist",
    ]

    # A second run with the same manifest is idempotent.
    session = _session(app_modules)
    try:
        sync._validate_manifest(session, manifest)
        replay = sync._sync(session, manifest)
    finally:
        session.close()
    assert replay["created_roles"] == []
    assert replay["added_memberships"] == []
    assert replay["set_mappings"] == []

    # The domain API reflects the synced state.
    users = {u["name"]: u["domain_roles"] for u in client.get("/api/users").get_json()}
    assert users["Employee"] == ["Geophysicist"]
    assert users["Staff Member"] == ["Geophysicist"]
    assert users["Supervisor"] == ["Petrophysicist"]

    session = _session(app_modules)
    try:
        mapping = workflow.domain_roles.get_task_mapping(
            session, "Seismic Signature Validation")
        assert mapping["role_name"] == "Geophysicist"
    finally:
        session.close()


def test_sync_script_rejects_unknown_users_tasks_and_undeclared_roles(
        client, app_modules):
    import scripts.sync_domain_assignments as sync

    bad_user = {
        "roles": [{"name": "R"}],
        "memberships": [{"user": "Nobody", "role": "R"}],
        "mappings": [],
    }
    session = _session(app_modules)
    try:
        with pytest.raises(ValueError, match="Unknown or inactive user"):
            sync._validate_manifest(session, bad_user)
    finally:
        session.close()

    bad_task = {
        "roles": [{"name": "R"}],
        "memberships": [{"user": "Employee", "role": "R"}],
        "mappings": [{"task": "Not a Real Step", "role": "R"}],
    }
    session = _session(app_modules)
    try:
        with pytest.raises(ValueError, match="Unknown task name"):
            sync._validate_manifest(session, bad_task)
    finally:
        session.close()

    bad_role_ref = {
        "roles": [{"name": "R"}],
        "memberships": [{"user": "Employee", "role": "Other"}],
        "mappings": [],
    }
    session = _session(app_modules)
    try:
        with pytest.raises(ValueError, match="Membership references undeclared role"):
            sync._validate_manifest(session, bad_role_ref)
    finally:
        session.close()


@pytest.mark.parametrize("section", ["roles", "memberships", "mappings"])
@pytest.mark.parametrize("malformed", [{}, "", None, 0, False])
def test_sync_script_rejects_falsy_non_array_sections(
        client, app_modules, section, malformed):
    """Falsy malformed sections must not be interpreted as an exact-empty sync."""
    import scripts.sync_domain_assignments as sync

    manifest = {"roles": [], "memberships": [], "mappings": []}
    manifest[section] = malformed
    session = _session(app_modules)
    try:
        with pytest.raises(ValueError, match=f"{section} must be an array"):
            sync._validate_manifest(session, manifest)
    finally:
        session.close()


def test_sync_script_records_deletion_markers_for_removed_mappings(client, app_modules):
    """Deleting a mapping records an app_settings marker so bootstrap skips it."""
    import scripts.sync_domain_assignments as sync
    import migrations

    session = _session(app_modules)
    try:
        manifest = {
            "roles": [{"name": "Petrophysicist"}],
            "memberships": [{"user": "Employee", "role": "Petrophysicist"}],
            "mappings": [],
        }
        sync._validate_manifest(session, manifest)
        summary = sync._sync(session, manifest)
    finally:
        session.close()

    conn = raw_sqlite_connect(client.db_path)
    try:
        marker = conn.execute(
            "SELECT key FROM app_settings WHERE key = ?",
            ("mapping_deleted:Quicklook Logs",)
        ).fetchone()
        assert marker is not None, "deletion marker should exist after sync removes a default mapping"

        session2 = _session(app_modules)
        try:
            migrations._ensure_default_domain_roles(session2)
        finally:
            session2.close()

        mapping = conn.execute(
            "SELECT task_name FROM task_domain_role_mappings WHERE task_name = ?",
            ("Quicklook Logs",)
        ).fetchone()
        assert mapping is None, "bootstrap must NOT recreate a mapping that was intentionally deleted"
    finally:
        conn.close()


def test_bootstrap_respects_deletion_markers_for_all_default_mappings(client, app_modules):
    """Both Quicklook Logs and Final Log Analysis stay deleted after bootstrap."""
    import scripts.sync_domain_assignments as sync
    import migrations

    session = _session(app_modules)
    try:
        manifest = {
            "roles": [{"name": "Petrophysicist"}],
            "memberships": [],
            "mappings": [],
        }
        sync._validate_manifest(session, manifest)
        sync._sync(session, manifest)
    finally:
        session.close()

    conn = raw_sqlite_connect(client.db_path)
    try:
        session2 = _session(app_modules)
        try:
            migrations._ensure_default_domain_roles(session2)
        finally:
            session2.close()

        for task_name in ["Quicklook Logs", "Final Log Analysis"]:
            mapping = conn.execute(
                "SELECT task_name FROM task_domain_role_mappings WHERE task_name = ?",
                (task_name,)
            ).fetchone()
            assert mapping is None, f"bootstrap must not recreate deleted mapping for {task_name}"
    finally:
        conn.close()


def test_sync_script_clears_deletion_marker_when_readding_mapping(client, app_modules):
    """Re-adding a previously-deleted mapping clears its deletion marker."""
    import scripts.sync_domain_assignments as sync
    import migrations

    session = _session(app_modules)
    try:
        manifest_empty = {
            "roles": [{"name": "Petrophysicist"}],
            "memberships": [{"user": "Employee", "role": "Petrophysicist"}],
            "mappings": [],
        }
        sync._validate_manifest(session, manifest_empty)
        sync._sync(session, manifest_empty)
    finally:
        session.close()

    conn = raw_sqlite_connect(client.db_path)
    try:
        marker = conn.execute(
            "SELECT key FROM app_settings WHERE key = ?",
            ("mapping_deleted:Quicklook Logs",)
        ).fetchone()
        assert marker is not None

        session2 = _session(app_modules)
        try:
            manifest_with_mapping = {
                "roles": [{"name": "Petrophysicist"}],
                "memberships": [{"user": "Employee", "role": "Petrophysicist"}],
                "mappings": [{"task": "Quicklook Logs", "role": "Petrophysicist"}],
            }
            sync._validate_manifest(session2, manifest_with_mapping)
            sync._sync(session2, manifest_with_mapping)
        finally:
            session2.close()

        marker = conn.execute(
            "SELECT key FROM app_settings WHERE key = ?",
            ("mapping_deleted:Quicklook Logs",)
        ).fetchone()
        assert marker is None, "re-adding a mapping should clear its deletion marker"

        mapping = conn.execute(
            "SELECT task_name FROM task_domain_role_mappings WHERE task_name = ?",
            ("Quicklook Logs",)
        ).fetchone()
        assert mapping is not None, "mapping should exist after re-adding"
    finally:
        conn.close()


def test_sync_script_records_deletion_markers_for_removed_roles(client, app_modules):
    """Deleting a role records an app_settings marker so bootstrap skips it."""
    import scripts.sync_domain_assignments as sync
    import migrations

    session = _session(app_modules)
    try:
        manifest = {
            "roles": [{"name": "Petrophysicist"}],
            "memberships": [],
            "mappings": [],
        }
        sync._validate_manifest(session, manifest)
        sync._sync(session, manifest)
    finally:
        session.close()

    conn = raw_sqlite_connect(client.db_path)
    try:
        for role_name in ["Inversion Expert", "Bureaucratic", "Structural Geologist", "Slides Designer"]:
            marker = conn.execute(
                "SELECT key FROM app_settings WHERE key = ?",
                (f"role_deleted:{role_name.lower()}",)
            ).fetchone()
            assert marker is not None, f"deletion marker should exist for {role_name}"

        session2 = _session(app_modules)
        try:
            migrations._ensure_default_domain_roles(session2)
        finally:
            session2.close()

        for role_name in ["Inversion Expert", "Bureaucratic", "Structural Geologist", "Slides Designer"]:
            role = conn.execute(
                "SELECT role_name FROM domain_roles WHERE role_name = ?",
                (role_name,)
            ).fetchone()
            assert role is None, f"bootstrap must NOT recreate role {role_name} that was intentionally deleted"
    finally:
        conn.close()


def test_bootstrap_skips_mappings_for_roles_with_deletion_markers(client, app_modules):
    """When a role has a deletion marker, its default mappings are also skipped."""
    import scripts.sync_domain_assignments as sync
    import migrations

    session = _session(app_modules)
    try:
        manifest = {
            "roles": [],
            "memberships": [],
            "mappings": [],
        }
        sync._validate_manifest(session, manifest)
        sync._sync(session, manifest)
    finally:
        session.close()

    conn = raw_sqlite_connect(client.db_path)
    try:
        marker = conn.execute(
            "SELECT key FROM app_settings WHERE key = ?",
            ("role_deleted:petrophysicist",)
        ).fetchone()
        assert marker is not None, "Petrophysicist should have a deletion marker"

        session2 = _session(app_modules)
        try:
            migrations._ensure_default_domain_roles(session2)
        finally:
            session2.close()

        for task_name in ["Quicklook Logs", "Final Log Analysis"]:
            mapping = conn.execute(
                "SELECT task_name FROM task_domain_role_mappings WHERE task_name = ?",
                (task_name,)
            ).fetchone()
            assert mapping is None, f"mapping {task_name} should not be created when its role is deleted"
    finally:
        conn.close()


def test_sync_script_clears_role_deletion_marker_when_readding_role(client, app_modules):
    """Re-adding a previously-deleted role clears its deletion marker."""
    import scripts.sync_domain_assignments as sync

    session = _session(app_modules)
    try:
        manifest_empty = {
            "roles": [{"name": "Petrophysicist"}],
            "memberships": [],
            "mappings": [],
        }
        sync._validate_manifest(session, manifest_empty)
        sync._sync(session, manifest_empty)
    finally:
        session.close()

    conn = raw_sqlite_connect(client.db_path)
    try:
        marker = conn.execute(
            "SELECT key FROM app_settings WHERE key = ?",
            ("role_deleted:inversion expert",)
        ).fetchone()
        assert marker is not None

        session2 = _session(app_modules)
        try:
            manifest_with_role = {
                "roles": [{"name": "Petrophysicist"}, {"name": "Inversion Expert"}],
                "memberships": [],
                "mappings": [],
            }
            sync._validate_manifest(session2, manifest_with_role)
            sync._sync(session2, manifest_with_role)
        finally:
            session2.close()

        marker = conn.execute(
            "SELECT key FROM app_settings WHERE key = ?",
            ("role_deleted:inversion expert",)
        ).fetchone()
        assert marker is None, "re-adding a role should clear its deletion marker"

        role = conn.execute(
            "SELECT role_name FROM domain_roles WHERE role_name = ?",
            ("Inversion Expert",)
        ).fetchone()
        assert role is not None, "role should exist after re-adding"
    finally:
        conn.close()


def test_readding_default_role_with_different_case_preserves_mapping_deletions(
        client, app_modules):
    """Role re-addition must not revive default mappings omitted by exact sync."""
    import migrations
    import scripts.sync_domain_assignments as sync

    session = _session(app_modules)
    try:
        empty = {"roles": [], "memberships": [], "mappings": []}
        sync._validate_manifest(session, empty)
        sync._sync(session, empty)
    finally:
        session.close()

    session = _session(app_modules)
    try:
        readded = {
            "roles": [{"name": "PETROPHYSICIST"}],
            "memberships": [],
            "mappings": [],
        }
        sync._validate_manifest(session, readded)
        sync._sync(session, readded)
    finally:
        session.close()

    session = _session(app_modules)
    try:
        with app_modules[1].write_transaction(session):
            migrations._ensure_default_domain_roles(session)
    finally:
        session.close()

    conn = raw_sqlite_connect(client.db_path)
    try:
        role = conn.execute(
            "SELECT role_name FROM domain_roles WHERE LOWER(role_name) = ?",
            ("petrophysicist",),
        ).fetchall()
        assert [row["role_name"] for row in role] == ["PETROPHYSICIST"]
        for task_name in ["Quicklook Logs", "Final Log Analysis"]:
            marker = conn.execute(
                "SELECT key FROM app_settings WHERE key = ?",
                (f"mapping_deleted:{task_name}",),
            ).fetchone()
            assert marker is not None
            mapping = conn.execute(
                "SELECT task_name FROM task_domain_role_mappings WHERE task_name = ?",
                (task_name,),
            ).fetchone()
            assert mapping is None
    finally:
        conn.close()


def test_v16_merges_overlapping_case_variant_memberships(client, app_modules):
    """The v16 merge coalesces users present in both role-name variants."""
    import migrations

    conn = raw_sqlite_connect(client.db_path)
    try:
        conn.execute("DROP INDEX uq_domain_roles_role_name_lower")
        canonical_id = conn.execute(
            "SELECT role_id FROM domain_roles WHERE role_name = ?",
            ("Petrophysicist",),
        ).fetchone()["role_id"]
        duplicate_id = conn.execute(
            "INSERT INTO domain_roles (role_name, created_at) VALUES (?, ?)",
            ("petrophysicist", "2024-02-01T00:00:00Z"),
        ).lastrowid
        conn.execute(
            "INSERT INTO domain_role_memberships "
            "(user_name, role_id, is_active, created_at) VALUES (?, ?, ?, ?)",
            ("Employee", canonical_id, 0, "2024-02-02T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO domain_role_memberships "
            "(user_name, role_id, is_active, created_at) VALUES (?, ?, ?, ?)",
            ("Employee", duplicate_id, 1, "2024-02-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO task_domain_role_mappings (task_name, role_id, created_at) "
            "VALUES (?, ?, ?)",
            ("Reservoir CoS", duplicate_id, "2024-02-01T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    session = _session(app_modules)
    try:
        with app_modules[1].write_transaction(session):
            migrations._migrate_v16_case_insensitive_roles(
                session, app_modules[1].get_engine())
    finally:
        session.close()

    conn = raw_sqlite_connect(client.db_path)
    try:
        roles = conn.execute(
            "SELECT role_id, role_name FROM domain_roles "
            "WHERE LOWER(role_name) = 'petrophysicist'"
        ).fetchall()
        assert [(row["role_id"], row["role_name"]) for row in roles] == [
            (canonical_id, "Petrophysicist")
        ]
        memberships = conn.execute(
            "SELECT role_id, is_active, created_at FROM domain_role_memberships "
            "WHERE user_name = ? AND role_id = ?",
            ("Employee", canonical_id),
        ).fetchall()
        assert [(row["role_id"], row["is_active"], row["created_at"])
                for row in memberships] == [
            (canonical_id, 1, "2024-02-01T00:00:00Z")
        ]
        mapping = conn.execute(
            "SELECT role_id FROM task_domain_role_mappings WHERE task_name = ?",
            ("Reservoir CoS",),
        ).fetchone()
        assert mapping["role_id"] == canonical_id
        with pytest.raises(sqlite3.IntegrityError,
                           match="uq_domain_roles_role_name_lower"):
            conn.execute(
                "INSERT INTO domain_roles (role_name, created_at) VALUES (?, ?)",
                ("PETROPHYSICIST", "2024-03-01T00:00:00Z"),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Activation semantics
# ---------------------------------------------------------------------------

def test_role_based_activation_assigns_the_correct_people(client, app_modules):
    _setup_role(app_modules, "Geophysicist", ["Employee", "Staff Member"],
                {"Lead Assessment": "Geophysicist"})

    pid = create_project(client, "DR-ACTIVATE-1")
    task = get_task_by_name(client, pid, "Lead Assessment")
    # The first task is reached at creation, so its mapped group activates.
    assert task["status"] == "In Progress"
    assert task["default_domain_role"] == "Geophysicist"
    assert sorted(a["name"] for a in task["assignees"]) == ["Employee", "Staff Member"]
    assert {a["source"] for a in task["assignees"]} == {"role"}
    assert task["assigned_to"] == "Employee"  # primary = first alphabetical


def test_unmapped_task_stays_not_assigned_on_activation(client, app_modules):
    pid = create_project(client, "DR-UNMAPPED-1")
    task = get_task_by_name(client, pid, "Reservoir CoS")
    assert task["default_domain_role"] is None

    session = _session(app_modules)
    try:
        assert _activate(session, task["task_id"]) == []
    finally:
        session.close()

    task = get_task_by_name(client, pid, "Reservoir CoS")
    assert task["status"] == "Not Assigned"
    assert task["assignees"] == []


def test_empty_role_leaves_task_not_assigned(client, app_modules):
    _setup_role(app_modules, "EmptyRole", [],
                {"Lead Assessment": "EmptyRole"})

    pid = create_project(client, "DR-EMPTY-1")
    assert get_task_by_name(client, pid, "Lead Assessment")["status"] == "Not Assigned"


def test_manual_preassignment_is_preserved_by_role_activation(client, app_modules):
    _setup_role(app_modules, "Geo", ["Staff Member"],
                {"Seismic Signature Validation": "Geo"})

    pid = create_project(client, "DR-PREASSIGN-1")
    task = get_task_by_name(client, pid, "Seismic Signature Validation")

    # Manually assign Employee before the role activation.
    resp = client.post(f"/api/tasks/{task['task_id']}/assign", json={
        "assigned_to": "Employee", "cascade": False, "revision": task["revision"],
    })
    assert resp.status_code == 200

    # The silent future preassignment becomes active only once earlier work is
    # finished. Approve preceding tasks through the domain save_task (the same
    # path the guarded PATCH used to take) so activation is explicit.
    import db as dbmod
    import workflow
    for earlier in get_tasks(client, pid):
        if earlier["sequence_no"] >= task["sequence_no"]:
            break
        session = dbmod.new_session()
        try:
            workflow.save_task(session, earlier["task_id"], {"status": "Approved"})
        finally:
            session.close()

    session = _session(app_modules)
    try:
        assigned = _activate(session, task["task_id"])
        assert sorted(assigned) == ["Employee", "Staff Member"]
    finally:
        session.close()

    task = get_task_by_name(client, pid, "Seismic Signature Validation")
    by_name = {a["name"]: a["source"] for a in task["assignees"]}
    assert by_name == {"Employee": "manual", "Staff Member": "role"}


def test_activation_only_moves_not_assigned_tasks(client, app_modules):
    _setup_role(app_modules, "Geo", ["Employee"],
                {"Seismic Signature Validation": "Geo"})

    pid = create_project(client, "DR-ONLYONCE-1")
    task = get_task_by_name(client, pid, "Seismic Signature Validation")
    client.post(f"/api/tasks/{task['task_id']}/assign", json={
        "assigned_to": "Staff Member", "cascade": False, "revision": task["revision"],
    })

    # Approve preceding tasks through the domain save_task so activation is
    # explicit and we can observe that a second activation is a no-op.
    import db as dbmod
    import workflow
    for earlier in get_tasks(client, pid):
        if earlier["sequence_no"] >= task["sequence_no"]:
            break
        session = dbmod.new_session()
        try:
            workflow.save_task(session, earlier["task_id"], {"status": "Approved"})
        finally:
            session.close()

    session = _session(app_modules)
    try:
        assigned = _activate(session, task["task_id"])
        assert sorted(assigned) == ["Employee", "Staff Member"]
    finally:
        session.close()

    task = get_task_by_name(client, pid, "Seismic Signature Validation")
    assert task["status"] == "In Progress"


def test_snapshot_behaviour_role_changes_do_not_reach_already_activated_tasks(
        client, app_modules):
    _setup_role(app_modules, "Geo", ["Employee"],
                {"Lead Assessment": "Geo", "Reservoir CoS": "Geo"})

    pid = create_project(client, "DR-SNAPSHOT-1")
    lead = get_task_by_name(client, pid, "Lead Assessment")
    assert lead["assigned_to"] == "Employee"

    # Role membership changes AFTER SSV was reached.
    import workflow
    session = _session(app_modules)
    try:
        role = workflow.domain_roles.get_role(session, "Geo")
        workflow.domain_roles.add_membership(session, "Staff Member", role["role_id"])
    finally:
        session.close()

    # Now reach Reservoir CoS. The role snapshot for Lead Assessment is already
    # taken, but the newly-reached step sees the current role membership.
    approve_task(client, lead["task_id"])
    session = _session(app_modules)
    try:
        _activate_next(session, pid)
    finally:
        session.close()

    # Lead Assessment keeps its original assignee; the newly-reached step gets
    # the new member.
    assert get_task_by_name(client, pid, "Lead Assessment")["assigned_to"] == "Employee"
    rc = get_task_by_name(client, pid, "Reservoir CoS")
    assert sorted(a["name"] for a in rc["assignees"]) == ["Employee", "Staff Member"]


# ---------------------------------------------------------------------------
# Notifications and board visibility
# ---------------------------------------------------------------------------

def test_role_activation_creates_assigned_notifications(client, app_modules):
    _setup_role(app_modules, "Geo", ["Employee", "Staff Member"],
                {"Lead Assessment": "Geo"})

    pid = create_project(client, "DR-NOTIFY-1")
    task = get_task_by_name(client, pid, "Lead Assessment")

    conn = raw_sqlite_connect(client.db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT recipient, event FROM notifications WHERE task_id = ? ORDER BY recipient",
            (task["task_id"],))]
    finally:
        conn.close()
    assert rows == [
        {"recipient": "Employee", "event": "assigned"},
        {"recipient": "Staff Member", "event": "assigned"},
    ]


def test_role_assignees_show_on_the_board(client, app_modules):
    _setup_role(app_modules, "Geo", ["Employee"],
                {"Lead Assessment": "Geo"})

    pid = create_project(client, "DR-BOARD-1")

    row = _row(client, pid)
    assert "Employee" in row["assignees"]
    assert row["current_owner"] == "Employee"


def test_manual_assignment_still_works_and_overrides_board(client, app_modules):
    pid = create_project(client, "DR-MANUAL-1")
    task = get_task_by_name(client, pid, "Lead Assessment")

    resp = client.post(f"/api/tasks/{task['task_id']}/assign", json={
        "assigned_to": "Employee", "cascade": False, "revision": task["revision"],
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["task"]["assigned_to"] == "Employee"
    assert body["task"]["assignees"] == [{"name": "Employee", "source": "manual", "notified": True}]

    row = _row(client, pid)
    assert row["current_owner"] == "Employee"


# ---------------------------------------------------------------------------
# Creation no longer auto-assigns
# ---------------------------------------------------------------------------

def test_new_prospect_project_leaves_every_step_not_assigned(client):
    pid = create_project(client, "DR-FRESH-1")
    for task in get_tasks(client, pid):
        assert task["status"] == "Not Assigned", task["task_name"]
        assert task["assignees"] == [], task["task_name"]


def test_bp_project_is_also_unassigned_at_creation(client):
    pid = create_project(client, "DR-FRESH-BP-1", pipeline_type="bp",
                         business_plan_enabled=True, business_plan_year=2027)
    for task in get_tasks(client, pid):
        assert task["status"] == "Not Assigned", task["task_name"]


def test_new_lead_has_empty_assignees_and_no_current_owner(client):
    pid = create_project(client, "DR-FRESH-BOARD-1")
    row = _row(client, pid)
    assert row["assignees"] == []
    assert row["current_owner"] is None
    assert row["lead_priority"] == "Low"


def test_fresh_bootstrap_seeds_default_roles_and_mappings(client, app_modules):
    import workflow

    session = _session(app_modules)
    try:
        assert [row["role_name"] for row in workflow.domain_roles.list_roles(session)] == [
            "Bureaucratic", "Inversion Expert", "Petrophysicist",
            "Slides Designer", "Structural Geologist",
        ]
        mappings = workflow.domain_roles.list_task_mappings(session)
        assert {(row["task_name"], row["role_name"]) for row in mappings} == {
            ("Final Log Analysis", "Petrophysicist"),
            ("Quicklook Logs", "Petrophysicist"),
        }
    finally:
        session.close()


def test_future_preassignment_is_silent_until_the_task_is_reached(client, app_modules):
    pid = create_project(client, "DR-FUTURE-SILENT-1")
    first, future = get_tasks(client, pid)[:2]

    response = client.post(f"/api/tasks/{future['task_id']}/assign", json={
        "assigned_to": "Employee", "cascade": False, "revision": future["revision"],
    })
    assert response.status_code == 200, response.get_json()
    future = response.get_json()["task"]
    assert future["status"] == "Not Assigned"
    assert future["actual_start"] is None
    assert future["assignees"] == [{"name": "Employee", "source": "manual", "notified": False}]

    conn = raw_sqlite_connect(client.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM notifications WHERE task_id = ?", (future["task_id"],)).fetchone()[0] == 0
    finally:
        conn.close()

    # Simulate completion of the preceding work via the domain save_task (the
    # same path the guarded PATCH used to take), then invoke the normal next
    # task driver. The saved manual group starts only at this point.
    import db as dbmod
    import workflow
    session = dbmod.new_session()
    try:
        workflow.save_task(session, first["task_id"], {"status": "Approved"})
    finally:
        session.close()
    session = _session(app_modules)
    try:
        assert _activate_next(session, pid) == ["Employee"]
    finally:
        session.close()

    future = get_task_by_name(client, pid, future["task_name"])
    assert future["status"] == "In Progress"
    assert future["assignees"] == [{"name": "Employee", "source": "manual", "notified": True}]


def test_owner_filter_matches_every_current_assignee(client):
    pid = create_project(client, "DR-OWNER-GROUP-1")
    task = get_tasks(client, pid)[0]
    assigned = client.post(f"/api/tasks/{task['task_id']}/assign", json={
        "assigned_to": "Employee", "cascade": False, "revision": task["revision"],
    })
    assert assigned.status_code == 200
    added = client.post(f"/api/tasks/{task['task_id']}/assignees", json={"add": ["Staff Member"]})
    assert added.status_code == 200, added.get_json()

    rows = client.get("/api/projects", query_string={
        "pipeline_filter": "prospect", "owner_filter": "Staff Member",
    }).get_json()
    assert [row["project_id"] for row in rows] == [pid]


def test_role_member_who_triggers_activation_is_notified(client, app_modules):
    _setup_role(app_modules, "Geo", ["Supervisor"], {"Lead Assessment": "Geo"})
    pid = create_project(client, "DR-ACTOR-NOTIFY-1", changed_by="Supervisor")
    task = get_task_by_name(client, pid, "Lead Assessment")

    conn = raw_sqlite_connect(client.db_path)
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT recipient, event FROM notifications WHERE task_id = ?", (task["task_id"],))]
    finally:
        conn.close()
    assert rows == [{"recipient": "Supervisor", "event": "assigned"}]
