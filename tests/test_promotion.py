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
from datetime import date

import pytest

from conftest import create_project, get_task_by_name, get_tasks


def test_promotion_sets_pipeline_type_and_captures_lead_summary(client):
    pid = create_project(client, "PROMO-1")
    lra = get_task_by_name(client, pid, "Lead Assessment")
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
    assert lead_summary["fields"]["Lead Assessment"]["lead_piip_gas_mean"] == "12.5"

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


def test_promotion_rejects_a_past_year(client):
    """A fresh promotion (business_plan_enabled flips false -> true) can't
    target a year before today's -- only Excel imports get that escape hatch
    (see tests/test_import.py, allow_historical_year=True)."""
    pid = create_project(client, "PROMO-PAST-1")
    resp = client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": date.today().year - 1,
    })
    assert resp.status_code == 400


def test_promotion_accepts_current_year_through_2035(client):
    pid_current = create_project(client, "PROMO-CURYEAR-1")
    resp = client.patch(f"/api/projects/{pid_current}/flags", json={
        "business_plan_enabled": True, "business_plan_year": date.today().year,
    })
    assert resp.status_code == 200

    pid_max = create_project(client, "PROMO-2035-1")
    resp = client.patch(f"/api/projects/{pid_max}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 2035,
    })
    assert resp.status_code == 200


def test_promotion_rejects_year_past_2035(client):
    pid = create_project(client, "PROMO-2036-1")
    resp = client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 2036,
    })
    assert resp.status_code == 400


def test_year_only_edit_of_already_enabled_well_keeps_the_wider_floor(client):
    """Once a well is promoted, changing only its year is not a fresh
    promotion -- the current-year floor doesn't apply, only the 1990-2040
    window that also admits imported historical wells."""
    pid = create_project(client, "PROMO-YEARONLY-1")
    resp = client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": date.today().year,
    })
    assert resp.status_code == 200

    resp = client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 1999,
    })
    assert resp.status_code == 200
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["business_plan_year"] == 1999


def test_demotion_preserves_snapshot_and_bp_task_statuses(client):
    pid = create_project(client, "DEMOTE-1")
    lra = get_task_by_name(client, pid, "Lead Assessment")
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
    assert detail["lead_summary"]["fields"]["Lead Assessment"]["lead_piip_gas_mean"] == "7.0"

    gate_after = get_task_by_name(client, pid, "BP Execution Gate")
    assert gate_after["status"] == gate_before["status"]


def test_recall_of_non_mature_lead_returns_it_unchanged(client):
    """Recalling an early-promoted well never fabricates approvals: every
    prospect step keeps its exact pre-recall status, so the record reappears
    on the maturation board where it left off. The Portfolio now includes
    every non-archived record, so the recalled lead is still there too --
    just as a 'Proposed' record, not a matured one."""
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
    row = next(r for r in rows if r["well_name"] == "RECALL-EARLY-1")
    assert row["status"] == "Proposed"


def test_recall_of_fully_matured_lead_stays_off_the_board(client):
    """Recalling a well whose prospect steps are ALL Approved keeps it off the
    maturation board: it derives as Completed and lands back in the Portfolio
    as a mature lead (Staked)."""
    import db as dbmod
    import workflow
    pid = create_project(client, "RECALL-MATURE-1")
    session = dbmod.new_session()
    try:
        for task in get_tasks(client, pid):
            if task["stage_group"] in workflow.PROSPECT_STAGES:
                workflow.ensure_task_approved(
                    session, task["task_id"], "Supervisor", automated=True)
    finally:
        session.close()
    client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 2027,
    })

    resp = client.patch(f"/api/projects/{pid}/flags", json={"business_plan_enabled": False})
    assert resp.status_code == 200

    board = client.get("/api/projects?pipeline_filter=prospect").get_json()
    assert "RECALL-MATURE-1" not in [p["project_name"] for p in board]
    rows = client.get("/api/portfolio/rows").get_json()["rows"]
    row = next(r for r in rows if r["well_name"] == "RECALL-MATURE-1")
    assert row["is_lead"] == 1
    assert row["status"] == "Staked"


def test_repromotion_refreshes_snapshot_timestamp(client):
    pid = create_project(client, "REPROMOTE-1")
    lra = get_task_by_name(client, pid, "Lead Assessment")
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
    # The guarded PATCH route no longer accepts status, so drive the setup
    # state through the domain save_task directly.
    pid = create_project(client, "BP-SURVIVE-1")
    proposal = get_task_by_name(client, pid, "Well Proposal")
    import db as dbmod
    import workflow
    session = dbmod.new_session()
    try:
        saved = workflow.save_task(session, proposal["task_id"], {
            "fields": {"sarh_formation_prognosis_pre_drill": "2500 ft"},
            "status": "Approved",
        })
    finally:
        session.close()
    assert saved["status"] == "Approved"

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
        "fields": {"p90_area_km2": "5"},
        "revision": task["revision"],
        "business_plan_enabled": True,
        "business_plan_year": 2027,
    })
    assert resp.status_code == 200, resp.get_json()
    # The save itself applied (dynamic fields round-trip through the detail payload).
    detail = client.get(f"/api/projects/{pid}/detail").get_json()
    assert detail["fields"]["Lead Assessment"]["p90_area_km2"] == "5"

    # The project was neither promoted nor given a year: /flags stays the
    # single writer of promotion state.
    assert _project_promotion_state(client, pid) == ("prospect", 0, None)


def test_overview_lead_ogip_composed_from_lead_piip_gas_mean_at_read(client):
    # The /detail overview is a read-time composition of task inputs (there is
    # no stored mirror): every save is reflected on the very next read.
    pid = create_project(client, "READ-COMPOSE-1")
    lra = get_task_by_name(client, pid, "Lead Assessment")
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


# ---------------------------------------------------------------------------
# Card 3X -- Active Drilling
# ---------------------------------------------------------------------------

def _into_post_drilling(client, project_id):
    """Put a BP well in the Post-Drilling stage, which is the only stage that
    may be marked as actively drilling.

    The gate is approved directly in the database: this file is about the FLAG,
    not about the gate's own approval path (which tests/
    test_business_plan_execution.py drives through the API end to end).
    """
    from conftest import get_task_by_name, raw_sqlite_connect
    gate = get_task_by_name(client, project_id, "BP Execution Gate")
    conn = raw_sqlite_connect(client.db_path)
    with conn:
        conn.execute("UPDATE project_tasks SET status = 'Approved' WHERE task_id = ?",
                     (gate["task_id"],))
    conn.close()
    for slug, keys in (
        ("well-letters", ("well_proposal_shared", "site_preparation_shared",
                          "approval_to_drill_shared")),
        ("gheer-inputs", ("gheer_geophysical_shared", "gheer_geomechanical_shared")),
    ):
        for key in keys:
            response = client.patch(
                f"/api/business-plan/wells/{project_id}/steps/{slug}/field",
                json={"field_key": key, "value": True})
            assert response.status_code == 200, response.get_json()


def _drilling_events(client, project_id):
    from conftest import raw_sqlite_connect
    conn = raw_sqlite_connect(client.db_path)
    try:
        return [dict(row) for row in conn.execute(
            "SELECT old_status, new_status, changed_by FROM task_history "
            "WHERE project_id = ? AND action_type = 'Active Drilling Flag' "
            "ORDER BY history_id", (project_id,))]
    finally:
        conn.close()


def test_active_drilling_persists_and_audits_only_real_changes(client):
    pid = create_project(client, "DRILL-1", pipeline_type="bp",
                         business_plan_enabled=True, business_plan_year=2030)
    assert client.get(f"/api/projects/{pid}/detail").get_json()["project"]["active_drilling"] == 0
    # Only a Post-Drilling well may be marked; the refusal has its own tests in
    # tests/test_business_plan_execution.py.
    _into_post_drilling(client, pid)

    resp = client.patch(f"/api/projects/{pid}/flags", json={"active_drilling": True})
    assert resp.status_code == 200, resp.get_json()
    assert client.get(f"/api/projects/{pid}/detail").get_json()["project"]["active_drilling"] == 1
    assert len(_drilling_events(client, pid)) == 1

    # Saving the same state again is not an event: a trail full of no-ops hides
    # the toggles that mattered.
    client.patch(f"/api/projects/{pid}/flags", json={"active_drilling": True})
    assert len(_drilling_events(client, pid)) == 1

    client.patch(f"/api/projects/{pid}/flags", json={"active_drilling": False})
    events = _drilling_events(client, pid)
    assert len(events) == 2
    assert (events[0]["old_status"], events[0]["new_status"]) == ("0", "1")
    assert (events[1]["old_status"], events[1]["new_status"]) == ("1", "0")
    assert client.get(f"/api/projects/{pid}/detail").get_json()["project"]["active_drilling"] == 0


def test_active_drilling_defaults_off_and_is_never_inferred(client):
    """The card is explicit: never derived from stage, dates, rig days or
    incomplete steps, and never auto-toggled by a stage move."""
    pid = create_project(client, "DRILL-2", pipeline_type="bp",
                         business_plan_enabled=True, business_plan_year=2030)
    assert client.get(f"/api/projects/{pid}/detail").get_json()["project"]["active_drilling"] == 0
    rows = client.get("/api/projects?pipeline_filter=bp").get_json()
    assert next(r for r in rows if r["project_id"] == pid)["active_drilling"] == 0


def test_active_drilling_requires_an_authorized_role(client):
    """UI visibility is not authorization -- the endpoint enforces it."""
    pid = create_project(client, "DRILL-3", pipeline_type="bp",
                         business_plan_enabled=True, business_plan_year=2030)
    assert client.post("/api/login", json={"name": "Employee"}).status_code == 200
    resp = client.patch(f"/api/projects/{pid}/flags", json={"active_drilling": True})
    assert resp.status_code == 403
    assert _drilling_events(client, pid) == []


def test_active_drilling_changes_no_workflow_state(client):
    """It is an operational visual state and nothing else: no stage move, no
    completion, no approval, no KPI shift."""
    pid = create_project(client, "DRILL-4", pipeline_type="bp",
                         business_plan_enabled=True, business_plan_year=2030)
    before = client.get(f"/api/business-plan/dashboard?year=2030").get_json()
    well_before = next(w for w in before["wells"] if w["project_id"] == pid)

    client.patch(f"/api/projects/{pid}/flags", json={"active_drilling": True})

    after = client.get(f"/api/business-plan/dashboard?year=2030").get_json()
    well_after = next(w for w in after["wells"] if w["project_id"] == pid)
    assert well_after["stage_key"] == well_before["stage_key"]
    assert well_after["completed_count"] == well_before["completed_count"]
    assert well_after["all_states"] == well_before["all_states"]
    assert after["kpis"] == before["kpis"]
