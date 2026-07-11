"""Tests for identity: session login against the users table, roles, actor
stamping, optional AUTH_REQUIRED enforcement, /api/users, and the typed
StaleRevisionError.

Login only accepts names seeded from config.SEED_USERS (the ``users`` table);
the session stores the row's canonical casing and role. With AUTH_REQUIRED off
and no login, every data endpoint still behaves exactly as before users
existed (anonymous changed_by fallback, implicit supervisor role).
"""
from __future__ import annotations

import pytest

from conftest import create_project, get_tasks, raw_sqlite_connect

# Seeded by config.SEED_USERS on every bootstrap (see migrations._ensure_base_data).
SEEDED = [("Employee", "employee"), ("Staff Member", "staff"), ("Supervisor", "supervisor")]


# ---------------------------------------------------------------------------
# login / me / logout round-trip
# ---------------------------------------------------------------------------

def test_login_me_logout_round_trip(client):
    # auth_required mirrors config.AUTH_REQUIRED (off by default here).
    me = client.get("/api/me").get_json()
    assert me == {"authenticated": False, "name": None, "role": None, "auth_required": False}

    resp = client.post("/api/login", json={"name": "  Staff Member  "})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "name": "Staff Member", "role": "staff"}

    me = client.get("/api/me").get_json()
    assert me == {"authenticated": True, "name": "Staff Member", "role": "staff", "auth_required": False}

    resp = client.post("/api/logout")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}

    me = client.get("/api/me").get_json()
    assert me == {"authenticated": False, "name": None, "role": None, "auth_required": False}


def test_login_missing_or_bad_name_rejected(client):
    resp = client.post("/api/login", json={})
    assert resp.status_code == 400
    assert resp.get_json()["detail"] == "Name must be 1 to 80 characters."

    resp = client.post("/api/login", json={"name": "   "})
    assert resp.status_code == 400

    resp = client.post("/api/login", json={"name": "A" * 81})
    assert resp.status_code == 400


def test_login_rejects_unknown_name(client):
    resp = client.post("/api/login", json={"name": "Nobody In Particular"})
    assert resp.status_code == 401
    assert resp.get_json()["detail"] == "Unknown user."
    assert client.get("/api/me").get_json()["authenticated"] is False


def test_login_case_insensitive_returns_canonical_name(client):
    resp = client.post("/api/login", json={"name": "sUpErViSoR"})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "name": "Supervisor", "role": "supervisor"}

    me = client.get("/api/me").get_json()
    assert me == {"authenticated": True, "name": "Supervisor", "role": "supervisor", "auth_required": False}


# ---------------------------------------------------------------------------
# /api/users
# ---------------------------------------------------------------------------

def test_users_returns_seeded_users_ordered_by_name(client):
    resp = client.get("/api/users")
    assert resp.status_code == 200
    users = resp.get_json()
    assert users == [{"name": name, "role": role} for name, role in SEEDED]
    assert [u["name"] for u in users] == sorted(u["name"] for u in users)


def test_users_excludes_inactive(client):
    conn = raw_sqlite_connect(client.db_path)
    conn.execute("UPDATE users SET is_active = 0 WHERE name = 'Employee'")
    conn.commit()
    conn.close()

    names = [u["name"] for u in client.get("/api/users").get_json()]
    assert "Employee" not in names
    assert names == ["Staff Member", "Supervisor"]

    # Deactivated users can no longer log in either.
    resp = client.post("/api/login", json={"name": "Employee"})
    assert resp.status_code == 401
    assert resp.get_json()["detail"] == "Unknown user."


# ---------------------------------------------------------------------------
# Role helpers (require_role is first APPLIED to routes in a later phase;
# here we cover its raw behavior plus current_role()'s dev-mode default)
# ---------------------------------------------------------------------------

def test_current_role_dev_mode_defaults_to_supervisor(client, monkeypatch):
    import config
    import main
    monkeypatch.setattr(config, "AUTH_REQUIRED", False)
    with main.app.test_request_context("/api/projects"):
        assert main.current_role() == "supervisor"
        main.require_role("supervisor")  # passes: no exception
        with pytest.raises(PermissionError):
            main.require_role("staff", "employee")


def test_current_role_uses_session_role(client):
    resp = client.post("/api/login", json={"name": "Employee"})
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert sess["role"] == "employee"
    assert client.get("/api/me").get_json()["role"] == "employee"


# ---------------------------------------------------------------------------
# Shared passcode
# ---------------------------------------------------------------------------

def test_login_with_configured_passcode(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "SHARED_PASSCODE", "s3cret")

    resp = client.post("/api/login", json={"name": "Supervisor", "passcode": "wrong"})
    assert resp.status_code == 401
    assert resp.get_json()["detail"] == "Invalid passcode."
    assert client.get("/api/me").get_json()["authenticated"] is False

    resp = client.post("/api/login", json={"name": "Supervisor", "passcode": "s3cret"})
    assert resp.status_code == 200
    assert client.get("/api/me").get_json() == {"authenticated": True, "name": "Supervisor", "role": "supervisor", "auth_required": False}


# ---------------------------------------------------------------------------
# Server-stamped changed_by
# ---------------------------------------------------------------------------

def test_logged_in_identity_overrides_payload_changed_by(client):
    # Login with non-canonical casing on purpose: the audit trail must carry
    # the users-table spelling, never the typed one.
    resp = client.post("/api/login", json={"name": "staff member"})
    assert resp.status_code == 200

    resp = client.post("/api/projects", json={
        "project_name": "IDENTITY-1", "changed_by": "Web User",
    })
    assert resp.status_code == 201
    pid = resp.get_json()["project_id"]

    events = client.get(f"/api/activity?project_id={pid}").get_json()
    assert len(events) >= 1
    assert all(e["changed_by"] == "Staff Member" for e in events)


def test_anonymous_payload_changed_by_still_honored(client):
    resp = client.post("/api/projects", json={
        "project_name": "IDENTITY-ANON-1", "changed_by": "Bob",
    })
    assert resp.status_code == 201
    pid = resp.get_json()["project_id"]

    events = client.get(f"/api/activity?project_id={pid}").get_json()
    assert len(events) >= 1
    assert all(e["changed_by"] == "Bob" for e in events)


# ---------------------------------------------------------------------------
# AUTH_REQUIRED enforcement
# ---------------------------------------------------------------------------

def test_auth_required_blocks_api_until_login(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "AUTH_REQUIRED", True)

    resp = client.get("/api/projects")
    assert resp.status_code == 401
    assert resp.get_json()["detail"] == "Authentication required."

    # Exempt endpoints stay open. /api/users must be reachable anonymously:
    # the login dialog needs the name list before a session exists.
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/users").status_code == 200
    assert client.post("/api/logout").status_code == 200

    # /api/me stays open AND advertises auth_required: True so the front-end
    # fronts the app with the full-page login before any data/meta load.
    me = client.get("/api/me").get_json()
    assert me == {"authenticated": False, "name": None, "role": None, "auth_required": True}

    resp = client.post("/api/login", json={"name": "Supervisor"})
    assert resp.status_code == 200

    resp = client.get("/api/projects")
    assert resp.status_code == 200
    resp = client.post("/api/projects", json={"project_name": "AUTHED-1"})
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# StaleRevisionError
# ---------------------------------------------------------------------------

def test_stale_revision_error_type_and_unit_raise(client):
    import db as dbmod
    import workflow

    assert issubclass(workflow.StaleRevisionError, RuntimeError)

    pid = create_project(client, "STALE-UNIT-1")
    task_id = get_tasks(client, pid)[0]["task_id"]

    session = dbmod.new_session()
    try:
        with pytest.raises(workflow.StaleRevisionError):
            workflow.save_task(session, task_id, {"status": "In Progress", "revision": 999})
    finally:
        session.close()
