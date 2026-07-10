"""Tests for the Phase 5 identity capture (session login, actor stamping,
optional AUTH_REQUIRED enforcement, and the typed StaleRevisionError).

All additions are strictly non-breaking: with AUTH_REQUIRED off and no login,
every existing endpoint behaves byte-identically to before.
"""
from __future__ import annotations

import pytest

from conftest import create_project, get_tasks


# ---------------------------------------------------------------------------
# login / me / logout round-trip
# ---------------------------------------------------------------------------

def test_login_me_logout_round_trip(client):
    me = client.get("/api/me").get_json()
    assert me == {"authenticated": False, "name": None}

    resp = client.post("/api/login", json={"name": "  Alice  "})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "name": "Alice"}

    me = client.get("/api/me").get_json()
    assert me == {"authenticated": True, "name": "Alice"}

    resp = client.post("/api/logout")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}

    me = client.get("/api/me").get_json()
    assert me == {"authenticated": False, "name": None}


def test_login_missing_or_bad_name_rejected(client):
    resp = client.post("/api/login", json={})
    assert resp.status_code == 400
    assert resp.get_json()["detail"] == "Name must be 1 to 80 characters."

    resp = client.post("/api/login", json={"name": "   "})
    assert resp.status_code == 400

    resp = client.post("/api/login", json={"name": "A" * 81})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Shared passcode
# ---------------------------------------------------------------------------

def test_login_with_configured_passcode(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "SHARED_PASSCODE", "s3cret")

    resp = client.post("/api/login", json={"name": "Alice", "passcode": "wrong"})
    assert resp.status_code == 401
    assert resp.get_json()["detail"] == "Invalid passcode."
    assert client.get("/api/me").get_json()["authenticated"] is False

    resp = client.post("/api/login", json={"name": "Alice", "passcode": "s3cret"})
    assert resp.status_code == 200
    assert client.get("/api/me").get_json() == {"authenticated": True, "name": "Alice"}


# ---------------------------------------------------------------------------
# Server-stamped changed_by
# ---------------------------------------------------------------------------

def test_logged_in_identity_overrides_payload_changed_by(client):
    resp = client.post("/api/login", json={"name": "Alice"})
    assert resp.status_code == 200

    resp = client.post("/api/projects", json={
        "project_name": "IDENTITY-1", "changed_by": "Web User",
    })
    assert resp.status_code == 201
    pid = resp.get_json()["project_id"]

    events = client.get(f"/api/activity?project_id={pid}").get_json()
    assert len(events) >= 1
    assert all(e["changed_by"] == "Alice" for e in events)


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

    # Exempt endpoints stay open.
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/me").status_code == 200
    assert client.post("/api/logout").status_code == 200

    resp = client.post("/api/login", json={"name": "Alice"})
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
