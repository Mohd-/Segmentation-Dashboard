"""Characterization tests for prospect -> BP Execution promotion/demotion.

Pins: lead summary snapshot capture, pipeline_type switch, year validation,
demotion preserving the snapshot and BP task statuses, re-promotion refreshing
the snapshot timestamp, the read-time lead_piip_gas_mean -> overview.lead_ogip
composition, and the derive-don't-store guarantee that promotion/demotion never
rewrite task status or data (applicability is a pure function of pipeline_type).
That purity routes the two recall outcomes: a fully matured record stays off
the maturation board (in the Portfolio as a mature lead), while an
early-promoted one returns to the board exactly where it left off.
"""
from __future__ import annotations

import time

import pytest

from conftest import create_project, get_task_by_name, get_tasks


def test_promotion_sets_pipeline_type_and_captures_lead_summary(client):
    pid = create_project(client, "PROMO-1")
    lra = get_task_by_name(client, pid, "Lead Resource Assessment")
    client.patch(f"/api/tasks/{lra['task_id']}/dynamic-fields", json={
        "fields": {"lead_piip_gas_mean": "12.5"},
    })

    resp = client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 2027,
    })
    assert resp.status_code == 200

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["pipeline_type"] == "bp"

    detail = client.get(f"/api/projects/{pid}/detail").get_json()
    lead_summary = detail["lead_summary"]
    assert lead_summary is not None
    assert lead_summary["fields"]["Lead Resource Assessment"]["lead_piip_gas_mean"] == "12.5"

    # v17 lifecycle: promotion opens the BP pipeline but no longer auto-assigns
    # its first step -- assignment (not promotion) moves a step to In Progress.
    gate = get_task_by_name(client, pid, "BP Execution Gate")
    assert gate["status"] == "Not Assigned"


def test_promotion_year_validation(client):
    pid = create_project(client, "PROMO-YEAR-1")
    # Floor is 1990 (admits imported historical wells); 1989 stays invalid.
    resp = client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 1989,
    })
    assert resp.status_code == 400

    resp = client.patch(f"/api/projects/{pid}/flags", json={"business_plan_enabled": True})
    assert resp.status_code == 400


def test_demotion_preserves_snapshot_and_bp_task_statuses(client):
    pid = create_project(client, "DEMOTE-1")
    lra = get_task_by_name(client, pid, "Lead Resource Assessment")
    client.patch(f"/api/tasks/{lra['task_id']}/dynamic-fields", json={
        "fields": {"lead_piip_gas_mean": "7.0"},
    })
    client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 2027,
    })
    gate_before = get_task_by_name(client, pid, "BP Execution Gate")

    resp = client.patch(f"/api/projects/{pid}/flags", json={"business_plan_enabled": False})
    assert resp.status_code == 200

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["pipeline_type"] == "prospect"
    assert project["business_plan_enabled"] == 0
    assert project["business_plan_year"] is None

    detail = client.get(f"/api/projects/{pid}/detail").get_json()
    assert detail["lead_summary"] is not None
    assert detail["lead_summary"]["fields"]["Lead Resource Assessment"]["lead_piip_gas_mean"] == "7.0"

    gate_after = get_task_by_name(client, pid, "BP Execution Gate")
    assert gate_after["status"] == gate_before["status"]


def test_recall_of_non_mature_lead_returns_it_unchanged(client):
    """Recalling an early-promoted well never fabricates approvals: every
    prospect step keeps its exact pre-recall status, so the record reappears
    on the maturation board where it left off (and, not being mature, it is
    not in the Portfolio)."""
    import workflow
    pid = create_project(client, "RECALL-EARLY-1")
    client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 2027,
    })
    statuses_before = {t["task_id"]: t["status"] for t in get_tasks(client, pid)
                       if t["stage_group"] in workflow.PROSPECT_STAGES}
    assert statuses_before  # fresh project: nothing approved

    resp = client.patch(f"/api/projects/{pid}/flags", json={"business_plan_enabled": False})
    assert resp.status_code == 200

    statuses_after = {t["task_id"]: t["status"] for t in get_tasks(client, pid)
                      if t["stage_group"] in workflow.PROSPECT_STAGES}
    assert statuses_after == statuses_before

    board = client.get("/api/projects?pipeline_filter=prospect").get_json()
    assert "RECALL-EARLY-1" in [p["project_name"] for p in board]
    rows = client.get("/api/portfolio/rows").get_json()["rows"]
    assert "RECALL-EARLY-1" not in [r["well_name"] for r in rows]


def test_recall_of_fully_matured_lead_stays_off_the_board(client):
    """Recalling a well whose prospect steps are ALL Approved keeps it off the
    maturation board: it derives as Completed and lands back in the Portfolio
    as a mature lead (Staked)."""
    import workflow
    pid = create_project(client, "RECALL-MATURE-1")
    for task in get_tasks(client, pid):
        if task["stage_group"] in workflow.PROSPECT_STAGES:
            resp = client.post(f"/api/tasks/{task['task_id']}/assign",
                               json={"assignee": "Supervisor", "cascade": False})
            assert resp.status_code == 200, resp.get_json()
            client.post(f"/api/tasks/{task['task_id']}/transition", json={"action": "submit"})
            resp = client.post(f"/api/tasks/{task['task_id']}/transition", json={"action": "approve"})
            assert resp.status_code == 200, resp.get_json()
    client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 2027,
    })

    resp = client.patch(f"/api/projects/{pid}/flags", json={"business_plan_enabled": False})
    assert resp.status_code == 200

    board = client.get("/api/projects?pipeline_filter=prospect").get_json()
    assert "RECALL-MATURE-1" not in [p["project_name"] for p in board]
    rows = client.get("/api/portfolio/rows").get_json()["rows"]
    row = next(r for r in rows if r["well_name"] == "RECALL-MATURE-1")
    assert row["is_mature_lead"] == 1
    assert row["status"] == "Staked"


def test_repromotion_refreshes_snapshot_timestamp(client):
    pid = create_project(client, "REPROMOTE-1")
    lra = get_task_by_name(client, pid, "Lead Resource Assessment")
    client.patch(f"/api/tasks/{lra['task_id']}/dynamic-fields", json={
        "fields": {"lead_piip_gas_mean": "3.3"},
    })
    client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 2027,
    })
    snap1 = client.get(f"/api/projects/{pid}/detail").get_json()["lead_summary"]["captured_at"]

    client.patch(f"/api/projects/{pid}/flags", json={"business_plan_enabled": False})
    time.sleep(1.1)  # captured_at has second resolution
    resp = client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 2028,
    })
    assert resp.status_code == 200

    snap2 = client.get(f"/api/projects/{pid}/detail").get_json()["lead_summary"]["captured_at"]
    assert snap2 != snap1

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["pipeline_type"] == "bp"
    assert project["business_plan_year"] == 2028


def test_bp_stage_data_entered_before_promotion_survives_promotion(client):
    # derive-don't-store: promotion is a pure pipeline switch. BP-stage work
    # entered while the record is still a prospect (status + dynamic fields)
    # must carry through promotion untouched -- no status/data rewrite.
    pid = create_project(client, "BP-SURVIVE-1")
    proposal = get_task_by_name(client, pid, "Well Proposal")
    saved = client.patch(f"/api/tasks/{proposal['task_id']}", json={
        "fields": {"sarh_formation_prognosis_pre_drill": "2500 ft"},
        "status": "Approved",
        "revision": proposal["revision"],
    })
    assert saved.status_code == 200, saved.get_json()
    assert saved.get_json()["task"]["status"] == "Approved"

    resp = client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 2030,
    })
    assert resp.status_code == 200

    after = get_task_by_name(client, pid, "Well Proposal")
    assert after["status"] == "Approved"  # status not rewritten by promotion
    detail = client.get(f"/api/projects/{pid}/detail").get_json()
    fields = detail["fields"]["Well Proposal"]
    assert fields["sarh_formation_prognosis_pre_drill"] == "2500 ft"

    # And demotion reverses the pipeline without disturbing the data either.
    resp = client.patch(f"/api/projects/{pid}/flags", json={"business_plan_enabled": False})
    assert resp.status_code == 200
    back = get_task_by_name(client, pid, "Well Proposal")
    assert back["status"] == "Approved"


# ---------------------------------------------------------------------------
# Single-writer guarantee: /flags is the ONLY route that mutates promotion
# state, and business_plan_enabled changes are supervisor-gated.
# ---------------------------------------------------------------------------

def _login(client, name):
    resp = client.post("/api/login", json={"name": name})
    assert resp.status_code == 200, resp.get_json()


def _project_promotion_state(client, pid):
    project = client.get(f"/api/projects/{pid}").get_json()
    return (project["pipeline_type"], project["business_plan_enabled"],
            project["business_plan_year"])


@pytest.mark.parametrize("name", ["Employee", "Staff Member"])
def test_flags_business_plan_requires_supervisor(client, name):
    pid = create_project(client, f"GATE-{name.split()[0].upper()}-1")
    _login(client, name)

    resp = client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 2027,
    })
    assert resp.status_code == 403
    assert "supervisor" in resp.get_json()["detail"]
    # Project promotion state must be completely untouched by the refused call.
    assert _project_promotion_state(client, pid) == ("prospect", 0, None)


def test_flags_business_plan_supervisor_promotes_and_recalls(client):
    pid = create_project(client, "GATE-SUP-1")
    _login(client, "Supervisor")

    resp = client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 2027,
    })
    assert resp.status_code == 200
    assert _project_promotion_state(client, pid) == ("bp", 1, 2027)

    resp = client.patch(f"/api/projects/{pid}/flags", json={"business_plan_enabled": False})
    assert resp.status_code == 200
    assert _project_promotion_state(client, pid) == ("prospect", 0, None)


def test_flags_business_plan_anonymous_dev_mode_still_works(client):
    # With AUTH_REQUIRED off and no session, current_role() is 'supervisor':
    # an open dev instance keeps promoting exactly as before roles existed.
    pid = create_project(client, "GATE-ANON-1")
    resp = client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 2028,
    })
    assert resp.status_code == 200
    assert _project_promotion_state(client, pid) == ("bp", 1, 2028)


def test_flags_active_well_only_stays_ungated_for_employee(client):
    pid = create_project(client, "GATE-AW-1")
    _login(client, "Employee")

    resp = client.patch(f"/api/projects/{pid}/flags", json={"active_well_enabled": True})
    assert resp.status_code == 200
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["active_well_enabled"] == 1
    # And the promotion state is untouched, of course.
    assert _project_promotion_state(client, pid) == ("prospect", 0, None)


def test_rename_ignores_smuggled_promotion_and_active_well_keys(client):
    pid = create_project(client, "SMUGGLE-RENAME-1")
    resp = client.patch(f"/api/projects/{pid}/rename", json={
        "new_name": "SMUGGLE-RENAME-1-NEW",
        "business_plan_enabled": True,
        "business_plan_year": 2027,
        "active_well_enabled": True,
    })
    assert resp.status_code == 200

    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["project_name"] == "SMUGGLE-RENAME-1-NEW"  # rename applied
    assert project["active_well_enabled"] == 0
    assert _project_promotion_state(client, pid) == ("prospect", 0, None)


def test_save_task_ignores_smuggled_business_plan_keys(client):
    pid = create_project(client, "SMUGGLE-SAVE-1")
    task = get_tasks(client, pid)[0]
    resp = client.patch(f"/api/tasks/{task['task_id']}", json={
        "status": "In Progress",
        "revision": task["revision"],
        "business_plan_enabled": True,
        "business_plan_year": 2027,
    })
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["task"]["status"] == "In Progress"  # save itself applied

    # The project was neither promoted nor given a year: /flags stays the
    # single writer of promotion state.
    assert _project_promotion_state(client, pid) == ("prospect", 0, None)


def test_overview_lead_ogip_composed_from_lead_piip_gas_mean_at_read(client):
    # The /detail overview is a read-time composition of task inputs (there is
    # no stored mirror): every save is reflected on the very next read.
    pid = create_project(client, "READ-COMPOSE-1")
    lra = get_task_by_name(client, pid, "Lead Resource Assessment")
    client.patch(f"/api/tasks/{lra['task_id']}/dynamic-fields", json={
        "fields": {"lead_piip_gas_mean": "12.5"},
    })
    detail = client.get(f"/api/projects/{pid}/detail").get_json()
    assert detail["overview"]["lead_ogip"] == "12.5"

    client.patch(f"/api/tasks/{lra['task_id']}/dynamic-fields", json={
        "fields": {"lead_piip_gas_mean": "13.0"},
    })
    detail = client.get(f"/api/projects/{pid}/detail").get_json()
    assert detail["overview"]["lead_ogip"] == "13.0"
