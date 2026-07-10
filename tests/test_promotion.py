"""Characterization tests for prospect -> BP Execution promotion/demotion.

Pins: lead summary snapshot capture, pipeline_type switch, BP task activation,
year validation, demotion preserving the snapshot and BP task statuses,
re-promotion refreshing the snapshot timestamp, and the lead_piip_gas_mean ->
overview.lead_ogip mirror.
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
