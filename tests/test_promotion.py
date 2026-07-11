"""Characterization tests for prospect -> BP Execution promotion/demotion.

Pins: lead summary snapshot capture, pipeline_type switch, year validation,
demotion preserving the snapshot and BP task statuses, re-promotion refreshing
the snapshot timestamp, the lead_piip_gas_mean -> overview.lead_ogip mirror, and
the derive-don't-store guarantee that promotion/demotion never rewrite task
status or data (applicability is a pure function of pipeline_type).
"""
from __future__ import annotations

import time

from conftest import create_project, get_task_by_name, raw_sqlite_connect


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
    resp = client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True, "business_plan_year": 2025,
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


def test_lead_piip_gas_mean_mirrors_to_overview_lead_ogip(client):
    pid = create_project(client, "MIRROR-1")
    lra = get_task_by_name(client, pid, "Lead Resource Assessment")
    client.patch(f"/api/tasks/{lra['task_id']}/dynamic-fields", json={
        "fields": {"lead_piip_gas_mean": "12.5"},
    })
    conn = raw_sqlite_connect(client.db_path)
    try:
        row = conn.execute(
            "SELECT lead_ogip FROM project_overview WHERE project_id = ?", (pid,),
        ).fetchone()
    finally:
        conn.close()
    assert row["lead_ogip"] == "12.5"
