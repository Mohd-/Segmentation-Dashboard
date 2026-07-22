"""Tests for the operator CLI tools (add_users.py, import_seismic_blocks.py)
and the per-user password login they enable.

The tool modules are imported lazily inside tests (never at module level) per
the conftest ordering requirement: env vars must be set before anything that
imports main/db is pulled in.
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# add_users.parse_user_spec
# ---------------------------------------------------------------------------

def test_parse_user_spec_name_role_password(client):
    import add_users
    assert add_users.parse_user_spec("Alice Smith:supervisor:s3cret") == \
        ("Alice Smith", "supervisor", "s3cret")


def test_parse_user_spec_password_optional_and_role_case_insensitive(client):
    import add_users
    assert add_users.parse_user_spec("Bob:Employee") == ("Bob", "employee", None)


def test_parse_user_spec_password_may_contain_colons(client):
    import add_users
    assert add_users.parse_user_spec("Bob:staff:a:b:c") == ("Bob", "staff", "a:b:c")


@pytest.mark.parametrize("spec, match", [
    ("JustAName", "expected name:role"),
    ("Alice:manager", "role must be one of"),
    (":supervisor", "name must be 1 to 80"),
    ("A" * 81 + ":staff", "name must be 1 to 80"),
    ("Alice:staff:", "password may not be blank"),
])
def test_parse_user_spec_rejects_bad_specs(client, spec, match):
    import add_users
    with pytest.raises(ValueError, match=match):
        add_users.parse_user_spec(spec)


# ---------------------------------------------------------------------------
# add_users.add_users + per-user password login
# ---------------------------------------------------------------------------

def _run_add_users(users, update_existing=False):
    import add_users
    import db
    session = db.new_session()
    try:
        return add_users.add_users(session, users, update_existing=update_existing)
    finally:
        session.close()


def test_add_users_inserts_and_skips_existing(client):
    added, updated, skipped = _run_add_users([
        ("Batch User A", "staff", None),
        ("Supervisor", "employee", None),  # seeded by config.SEED_USERS -> skipped
    ])
    assert added == ["Batch User A"]
    assert updated == []
    assert skipped == ["Supervisor"]

    names = {u["name"]: u["role"] for u in client.get("/api/users").get_json()}
    assert names["Batch User A"] == "staff"
    assert names["Supervisor"] == "supervisor"  # role untouched by the skip


def test_add_users_update_flag_overwrites_role(client):
    _run_add_users([("Batch User B", "employee", None)])
    added, updated, skipped = _run_add_users([("Batch User B", "supervisor", None)],
                                             update_existing=True)
    assert (added, updated, skipped) == ([], ["Batch User B"], [])
    names = {u["name"]: u["role"] for u in client.get("/api/users").get_json()}
    assert names["Batch User B"] == "supervisor"


def test_login_requires_password_when_user_has_one(client):
    _run_add_users([("Locked User", "staff", "hunter2")])

    resp = client.post("/api/login", json={"name": "Locked User"})
    assert resp.status_code == 401
    assert resp.get_json()["detail"] == "Invalid password."

    resp = client.post("/api/login", json={"name": "Locked User", "passcode": "wrong"})
    assert resp.status_code == 401

    # The front-end sends the login form's Passcode box as "passcode".
    resp = client.post("/api/login", json={"name": "Locked User", "passcode": "hunter2"})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "name": "Locked User", "role": "staff"}

    # An explicit "password" key works too.
    client.post("/api/logout")
    resp = client.post("/api/login", json={"name": "locked user", "password": "hunter2"})
    assert resp.status_code == 200


def test_update_without_password_keeps_stored_password(client):
    """A role-only --update spec must not silently strip an account's password
    (re-running --update against a password-less roster file would otherwise
    downgrade everyone to name-only login)."""
    _run_add_users([("Promoted User", "staff", "hunter2")])
    _run_add_users([("Promoted User", "supervisor", None)], update_existing=True)

    names = {u["name"]: u["role"] for u in client.get("/api/users").get_json()}
    assert names["Promoted User"] == "supervisor"
    resp = client.post("/api/login", json={"name": "Promoted User"})
    assert resp.status_code == 401  # password still required
    resp = client.post("/api/login", json={"name": "Promoted User", "passcode": "hunter2"})
    assert resp.status_code == 200


def test_per_user_password_supersedes_shared_passcode(client, monkeypatch):
    """The login form has ONE Passcode box: for a user with a stored password
    it must satisfy only the per-user check, not also the shared passcode
    (otherwise no single input could ever log that user in). Users without a
    stored password keep the shared-passcode behavior."""
    import config
    monkeypatch.setattr(config, "SHARED_PASSCODE", "teampass")
    _run_add_users([("Locked User", "staff", "hunter2")])

    resp = client.post("/api/login", json={"name": "Locked User", "passcode": "hunter2"})
    assert resp.status_code == 200

    client.post("/api/logout")
    resp = client.post("/api/login", json={"name": "Locked User", "passcode": "teampass"})
    assert resp.status_code == 401
    assert resp.get_json()["detail"] == "Invalid password."

    resp = client.post("/api/login", json={"name": "Supervisor", "passcode": "teampass"})
    assert resp.status_code == 200


def test_login_without_stored_password_stays_name_only(client):
    _run_add_users([("Open User", "employee", None)])
    resp = client.post("/api/login", json={"name": "Open User"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# import_seismic_blocks
# ---------------------------------------------------------------------------

def _write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_block_map_normalizes_and_dedupes(client, tmp_path):
    import import_seismic_blocks as isb
    source = _write_json(tmp_path / "blocks.json",
                         {" Block A ": [2525, "2525", " 88421 "], "Block B": []})
    assert isb.load_block_map(source) == {"Block A": ["2525", "88421"], "Block B": []}


@pytest.mark.parametrize("payload, match", [
    (["not", "a", "dict"], "top level must be an object"),
    ({"Block A": "2525"}, "must map to a LIST"),
    ({"Block A": ["2525", "  "]}, "blank AR entry"),
    ({"  ": ["2525"]}, "blank block name"),
    ({"Block A": [["nested"]]}, "non-scalar AR entry"),
])
def test_load_block_map_rejects_bad_shapes(client, tmp_path, payload, match):
    import import_seismic_blocks as isb
    source = _write_json(tmp_path / "bad.json", payload)
    with pytest.raises(ValueError, match=match):
        isb.load_block_map(source)


def test_merge_block_maps_unions_preserving_order(client):
    import import_seismic_blocks as isb
    existing = {"Block A": ["1", "2"], "Block B": ["3"]}
    incoming = {"Block A": ["2", "4"], "Block C": ["5"]}
    assert isb.merge_block_maps(existing, incoming) == {
        "Block A": ["1", "2", "4"], "Block B": ["3"], "Block C": ["5"],
    }


def test_duplicate_ars_across_blocks_reported(client):
    import import_seismic_blocks as isb
    dupes = isb.duplicate_ars_across_blocks({"Block A": ["1", "2"], "Block B": ["2"]})
    assert dupes == {"2": ["Block A", "Block B"]}
