"""Characterization tests for HTTP status codes and JSON response shapes.

These pin the *current* API contract (main.py routes backed by database.py) so a
SQLAlchemy refactor can be checked against them. Where actual behavior surprised
us relative to a naive reading of the spec, a comment marks it.
"""
from __future__ import annotations

import io
import json

import openpyxl
import pytest

import portfolio_export
from conftest import create_project, get_task_by_name, get_tasks


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------

def test_health_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.get_json()
    for key in ("ok", "app", "version", "backend", "db"):
        assert key in body
    assert body["ok"] is True
    # Pin the release label from config.APP_VERSION (v18 release; a product
    # axis distinct from the database schema version -- see config.py).
    assert body["version"] == "v18"


# ---------------------------------------------------------------------------
# /api/meta
# ---------------------------------------------------------------------------

def test_meta_shape_matches_workflow_constants(client):
    import workflow
    resp = client.get("/api/meta")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["prospect_stages"] == workflow.PROSPECT_STAGES
    assert body["bp_stages"] == workflow.BP_EXECUTION_STAGES
    assert body["stage_order"] == workflow.STAGE_ORDER
    # The 4 lifecycle statuses, straight from the domain layer.
    assert body["statuses"] == ["Not Assigned", "In Progress", "Ready", "Approved"]
    assert workflow.STATUSES == ["Not Assigned", "In Progress", "Ready", "Approved"]
    assert body["roles"] == ["supervisor", "staff", "employee"]
    # Block name -> [AR number, ...], feeding the Portfolio's dependent
    # Block/AR dropdowns (config.SEISMIC_BLOCK_AR_MAP, from seismic_blocks.json).
    assert "seismic_blocks" in body
    assert isinstance(body["seismic_blocks"], dict)
    for ars in body["seismic_blocks"].values():
        assert isinstance(ars, list)


# ---------------------------------------------------------------------------
# POST /api/projects
# ---------------------------------------------------------------------------

def test_create_project_valid(client):
    resp = client.post("/api/projects", json={"project_name": "ALPHA-1"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert "project_id" in body
    assert "folder_path" in body


def test_create_project_empty_name(client):
    resp = client.post("/api/projects", json={"project_name": ""})
    assert resp.status_code == 400
    assert "detail" in resp.get_json()


def test_create_project_duplicate_name(client):
    create_project(client, "DUP-1")
    resp = client.post("/api/projects", json={"project_name": "DUP-1"})
    assert resp.status_code == 400
    assert "detail" in resp.get_json()


def test_create_project_name_too_long(client):
    resp = client.post("/api/projects", json={"project_name": "A" * 121})
    assert resp.status_code == 400
    assert "detail" in resp.get_json()


@pytest.mark.parametrize("payload", [
    {"business_plan_enabled": True},
    {"business_plan_enabled": True, "business_plan_year": 2025},
    {"business_plan_enabled": True, "business_plan_year": 2041},
])
def test_create_project_bp_enabled_needs_valid_year(client, payload):
    body = {"project_name": "BADYEAR"}
    body.update(payload)
    resp = client.post("/api/projects", json=body)
    assert resp.status_code == 400
    assert "detail" in resp.get_json()


def test_create_project_bp_enabled_valid_year_ok(client):
    resp = client.post("/api/projects", json={
        "project_name": "GOODYEAR", "business_plan_enabled": True, "business_plan_year": 2026,
    })
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# GET /api/projects
# ---------------------------------------------------------------------------

def test_list_projects_row_shape(client):
    create_project(client, "ROWSHAPE-1")
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    rows = resp.get_json()
    assert len(rows) == 1
    row = rows[0]
    for key in (
        "project_id", "project_name", "current_stage", "current_task", "health",
        "current_task_priority", "has_high_priority_tasks", "active_drilling",
        "active_well_enabled",
    ):
        assert key in row, key


def test_list_projects_search_filter(client):
    create_project(client, "ALPHA-1")
    create_project(client, "BETA-2")
    resp = client.get("/api/projects?search=alpha")
    names = [p["project_name"] for p in resp.get_json()]
    assert names == ["ALPHA-1"]


def test_list_projects_pipeline_filter(client):
    create_project(client, "PROSPECT-A")
    create_project(client, "BP-A", pipeline_type="bp", business_plan_enabled=True, business_plan_year=2030)
    resp_bp = client.get("/api/projects?pipeline_filter=bp")
    assert [p["project_name"] for p in resp_bp.get_json()] == ["BP-A"]
    resp_prospect = client.get("/api/projects?pipeline_filter=prospect")
    assert [p["project_name"] for p in resp_prospect.get_json()] == ["PROSPECT-A"]


# ---------------------------------------------------------------------------
# GET /api/projects/<id>
# ---------------------------------------------------------------------------

def test_get_project_ok(client):
    pid = create_project(client, "GETME-1")
    resp = client.get(f"/api/projects/{pid}")
    assert resp.status_code == 200
    assert resp.get_json()["project_id"] == pid


def test_get_project_not_found(client):
    resp = client.get("/api/projects/999999")
    assert resp.status_code == 404
    assert resp.get_json()["detail"] == "Lead / well not found"


# ---------------------------------------------------------------------------
# GET /api/projects/<id>/detail
# ---------------------------------------------------------------------------

def test_project_detail_shape(client):
    pid = create_project(client, "DETAIL-1")
    resp = client.get(f"/api/projects/{pid}/detail")
    assert resp.status_code == 200
    body = resp.get_json()
    for key in ("project", "tasks", "completion", "fields", "lead_summary", "overview"):
        assert key in body
    assert "percent" in body["completion"]
    assert body["lead_summary"] is None  # never promoted
    # overview is composed from the task inputs at read time (no stored
    # project_overview table); derisking carries the computed Total CoS.
    assert isinstance(body["overview"], dict)
    assert "derisking" in body["overview"]


# ---------------------------------------------------------------------------
# PATCH /api/projects/<id>/rename
# ---------------------------------------------------------------------------

def test_rename_project_ok(client):
    pid = create_project(client, "RENAME-1")
    resp = client.patch(f"/api/projects/{pid}/rename", json={"new_name": "RENAME-1-NEW"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_rename_project_empty(client):
    pid = create_project(client, "RENAME-2")
    resp = client.patch(f"/api/projects/{pid}/rename", json={"new_name": ""})
    assert resp.status_code == 400


def test_rename_project_too_long(client):
    pid = create_project(client, "RENAME-3")
    resp = client.patch(f"/api/projects/{pid}/rename", json={"new_name": "B" * 121})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DELETE / PATCH restore
# ---------------------------------------------------------------------------

def test_delete_archives_and_restore_brings_back(client):
    pid = create_project(client, "ARCHIVE-1")
    resp = client.delete(f"/api/projects/{pid}")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "archived": True}

    ids = [p["project_id"] for p in client.get("/api/projects").get_json()]
    assert pid not in ids

    resp = client.patch(f"/api/projects/{pid}/restore")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    ids = [p["project_id"] for p in client.get("/api/projects").get_json()]
    assert pid in ids


# ---------------------------------------------------------------------------
# PATCH /api/tasks/<id> (save)
# ---------------------------------------------------------------------------

def test_save_task_ok(client):
    pid = create_project(client, "TASKSAVE-1")
    task = get_tasks(client, pid)[0]
    resp = client.patch(f"/api/tasks/{task['task_id']}", json={
        "status": "In Progress", "revision": task["revision"],
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "task" in body


def test_save_task_invalid_status(client):
    pid = create_project(client, "TASKSAVE-2")
    task = get_tasks(client, pid)[0]
    resp = client.patch(f"/api/tasks/{task['task_id']}", json={
        "status": "Bogus Status", "revision": task["revision"],
    })
    assert resp.status_code == 400


def test_save_task_stale_revision_conflict(client):
    pid = create_project(client, "TASKSAVE-3")
    task = get_tasks(client, pid)[0]
    stale_revision = task["revision"]
    resp1 = client.patch(f"/api/tasks/{task['task_id']}", json={
        "status": "In Progress", "revision": stale_revision,
    })
    assert resp1.status_code == 200
    resp2 = client.patch(f"/api/tasks/{task['task_id']}", json={
        "status": "Approved", "revision": stale_revision,
    })
    assert resp2.status_code == 409
    assert "detail" in resp2.get_json()


# ---------------------------------------------------------------------------
# POST /api/tasks/<id>/assign
# ---------------------------------------------------------------------------

def test_assign_task_shape_and_canonical_casing(client):
    pid = create_project(client, "ASSIGN-CONTRACT-1")
    task = get_tasks(client, pid)[0]
    # Lowercase on purpose: the response must carry the users-table casing.
    resp = client.post(f"/api/tasks/{task['task_id']}/assign", json={
        "assignee": "supervisor", "cascade": False, "revision": task["revision"],
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["task"]["assigned_to"] == "Supervisor"
    assert body["task"]["status"] == "In Progress"
    assert body["task"]["revision"] == task["revision"] + 1


def test_assign_task_stale_revision_conflict(client):
    pid = create_project(client, "ASSIGN-CONTRACT-2")
    task = get_tasks(client, pid)[0]
    resp = client.post(f"/api/tasks/{task['task_id']}/assign", json={
        "assignee": "Supervisor", "revision": task["revision"] + 5,
    })
    assert resp.status_code == 409
    assert "detail" in resp.get_json()


def test_assign_task_unknown_assignee(client):
    pid = create_project(client, "ASSIGN-CONTRACT-3")
    task = get_tasks(client, pid)[0]
    resp = client.post(f"/api/tasks/{task['task_id']}/assign", json={
        "assignee": "Nobody In Particular", "revision": task["revision"],
    })
    assert resp.status_code == 400
    assert resp.get_json()["detail"] == "Unknown or inactive user."


# ---------------------------------------------------------------------------
# POST /api/tasks/<id>/transition
# ---------------------------------------------------------------------------

def test_transition_task_shape(client):
    pid = create_project(client, "TRANSITION-CONTRACT-1")
    task = get_tasks(client, pid)[0]
    assigned = client.post(f"/api/tasks/{task['task_id']}/assign", json={
        "assignee": "Supervisor", "cascade": False, "revision": task["revision"],
    }).get_json()["task"]
    resp = client.post(f"/api/tasks/{task['task_id']}/transition", json={
        "action": "submit", "revision": assigned["revision"],
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["task"]["status"] == "Ready"
    assert body["task"]["revision"] == assigned["revision"] + 1


def test_transition_task_stale_revision_conflict(client):
    pid = create_project(client, "TRANSITION-CONTRACT-2")
    task = get_tasks(client, pid)[0]
    assigned = client.post(f"/api/tasks/{task['task_id']}/assign", json={
        "assignee": "Supervisor", "cascade": False, "revision": task["revision"],
    }).get_json()["task"]
    resp = client.post(f"/api/tasks/{task['task_id']}/transition", json={
        "action": "submit", "revision": assigned["revision"] + 5,
    })
    assert resp.status_code == 409
    assert "detail" in resp.get_json()


# ---------------------------------------------------------------------------
# GET /api/tasks/<id>
# ---------------------------------------------------------------------------

def test_get_task_ok(client):
    pid = create_project(client, "TASKGET-1")
    task = get_tasks(client, pid)[0]
    resp = client.get(f"/api/tasks/{task['task_id']}")
    assert resp.status_code == 200


def test_get_task_missing(client):
    resp = client.get("/api/tasks/999999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Dynamic fields round trip
# ---------------------------------------------------------------------------

def test_dynamic_fields_round_trip_wrapped_and_bare(client):
    pid = create_project(client, "DYNFIELDS-1")
    task = get_tasks(client, pid)[0]
    tid = task["task_id"]

    resp = client.patch(f"/api/tasks/{tid}/dynamic-fields", json={"fields": {"foo": "bar"}})
    assert resp.status_code == 200
    got = client.get(f"/api/tasks/{tid}/dynamic-fields").get_json()
    assert got == {"foo": "bar"}

    resp = client.patch(f"/api/tasks/{tid}/dynamic-fields", json={"baz": "qux"})
    assert resp.status_code == 200
    got = client.get(f"/api/tasks/{tid}/dynamic-fields").get_json()
    assert got == {"foo": "bar", "baz": "qux"}


# ---------------------------------------------------------------------------
# PATCH /api/tasks/<id>/priority
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [("Low", "Low"), ("Medium", "Medium"), ("High", "High")])
def test_set_task_priority_valid(client, value, expected):
    pid = create_project(client, f"PRIORITY-{value}")
    task = get_tasks(client, pid)[0]
    resp = client.patch(f"/api/tasks/{task['task_id']}/priority", json={"priority": value})
    assert resp.status_code == 200
    got = client.get(f"/api/tasks/{task['task_id']}").get_json()
    assert got["priority"] == expected


def test_set_task_priority_unknown_falls_back_to_medium(client):
    pid = create_project(client, "PRIORITY-BOGUS")
    task = get_tasks(client, pid)[0]
    # Actual behavior: the endpoint still returns 200 (it never validates and
    # rejects); invalid values are silently normalized to Medium.
    resp = client.patch(f"/api/tasks/{task['task_id']}/priority", json={"priority": "bogus"})
    assert resp.status_code == 200
    got = client.get(f"/api/tasks/{task['task_id']}").get_json()
    assert got["priority"] == "Medium"


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

def test_completion_percent_zero_for_new_project(client):
    pid = create_project(client, "COMPLETION-1")
    resp = client.get(f"/api/projects/{pid}/completion")
    assert resp.status_code == 200
    assert resp.get_json() == {"percent": 0.0}


# ---------------------------------------------------------------------------
# Business plan rows / portfolio
# ---------------------------------------------------------------------------

def test_business_plan_rows_and_portfolio_rows(client):
    create_project(client, "BPROW-1", pipeline_type="bp", business_plan_enabled=True, business_plan_year=2028)
    resp = client.get("/api/business-plan/rows")
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)

    resp = client.get("/api/portfolio/rows")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "rows" in body
    assert "summary" in body
    assert "business_plan_wells" in body["summary"]
    assert "cumulative_ogip" in body["summary"]
    # WS7: every row carries exactly the 8 analysis columns (plus internals).
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    for key in ("well_name", "gas_field", "seismic_block", "classification",
                "year", "fluid", "mean_ogip", "total_cos"):
        assert key in row, key


def test_portfolio_rows_invalid_year(client):
    resp = client.get("/api/portfolio/rows?year=1999")
    assert resp.status_code == 400
    assert "detail" in resp.get_json()


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------

def test_activity_contains_lead_created(client):
    pid = create_project(client, "ACTIVITY-1")
    resp = client.get("/api/activity")
    assert resp.status_code == 200
    events = resp.get_json()
    assert any(e["action_type"] == "Lead Created" and e["project_id"] == pid for e in events)


def test_activity_filters_by_project_id(client):
    pid1 = create_project(client, "ACTIVITY-A")
    pid2 = create_project(client, "ACTIVITY-B")
    resp = client.get(f"/api/activity?project_id={pid1}")
    events = resp.get_json()
    assert len(events) >= 1
    assert all(e["project_id"] == pid1 for e in events)


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

def test_export_excel(client):
    create_project(client, "EXPORT-1")
    # A BP well with a saved flowback field exercises the Portfolio Export
    # composer's task-input read path (WS4), not just an empty sheet.
    bp_pid = create_project(client, "EXPORT-BP-1", pipeline_type="bp",
                             business_plan_enabled=True, business_plan_year=2030)
    flowback_task = get_task_by_name(client, bp_pid, "Flowback Results")
    resp = client.patch(f"/api/tasks/{flowback_task['task_id']}/dynamic-fields",
                         json={"fields": {"flowback_dynamic_ogip_bcf": "12.5"}})
    assert resp.status_code == 200

    resp = client.get("/api/export/excel")
    assert resp.status_code == 200
    assert resp.content_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    workbook = openpyxl.load_workbook(io.BytesIO(resp.data))
    assert workbook.sheetnames == [
        "Executive Summary", "Wells Overview", "Task Register", "Monthly Progress",
        "Portfolio Export", "Staking Options",
    ]

    ws_export = workbook["Portfolio Export"]
    header = [cell.value for cell in ws_export[4]]
    assert header == portfolio_export.PORTFOLIO_EXPORT_COLUMNS

    well_col = header.index("Well Name")
    dyn_mean_col = header.index("Dynamic Mean (BCF)")
    matching_rows = [row for row in ws_export.iter_rows(min_row=5, max_row=ws_export.max_row, values_only=True)
                     if row[well_col] == "EXPORT-BP-1"]
    assert len(matching_rows) == 1
    assert matching_rows[0][dyn_mean_col] == "12.5"

    ws_staking = workbook["Staking Options"]
    staking_header = [cell.value for cell in ws_staking[4]]
    assert staking_header == portfolio_export.STAKING_EXPORT_COLUMNS

    # The choke column header matches the field's inch scale (flowback_choke_size_in
    # and the UI's 'Choke Size (in)'), not the legacy 1/64" convention.
    assert "Choke Size (in)" in header


def test_export_reservoir_cos_columns_read_one_primary_row(client):
    """A blank leading reservoir_cos_rows row must not split the export between
    vintages: Pull-up/Amplitude/BTS/CoS AND the Seismic Block AR all come from
    the ONE primary row (first row with a CoS pct or AR number filled), so the
    Excel row matches what the Portfolio UI derives."""
    bp_pid = create_project(client, "EXPORT-BP-2", pipeline_type="bp",
                             business_plan_enabled=True, business_plan_year=2030)
    reservoir_task = get_task_by_name(client, bp_pid, "Reservoir CoS")
    resp = client.patch(f"/api/tasks/{reservoir_task['task_id']}/dynamic-fields",
                         json={"fields": {"reservoir_cos_rows": json.dumps([
                             {"seismic_volume_ar_number": "", "reservoir_cos_pct": "",
                              "pull_up": "", "amplitude_ratio": "", "base_tight_sarah": ""},
                             {"seismic_volume_ar_number": "AR-777", "reservoir_cos_pct": "55",
                              "pull_up": "Yes", "amplitude_ratio": "0.8", "base_tight_sarah": "0.6"},
                         ])}})
    assert resp.status_code == 200

    resp = client.get("/api/export/excel")
    assert resp.status_code == 200
    workbook = openpyxl.load_workbook(io.BytesIO(resp.data))
    ws_export = workbook["Portfolio Export"]
    header = [cell.value for cell in ws_export[4]]
    matching_rows = [row for row in ws_export.iter_rows(min_row=5, max_row=ws_export.max_row, values_only=True)
                     if row[header.index("Well Name")] == "EXPORT-BP-2"]
    assert len(matching_rows) == 1
    row = matching_rows[0]
    assert row[header.index("Pull-up")] == "Yes"
    assert row[header.index("Amplitude Ratio")] == "0.8"
    assert row[header.index("BTS")] == "0.6"
    assert row[header.index("Reservoir CoS (%)")] == "55"
    # AR-777 is unmapped in the shipped block map, so the raw AR shows -- and it
    # comes from the SAME row as the four values above, never a different one.
    assert row[header.index("Seismic Block")] == "AR-777"


def test_export_includes_proposed_leads_with_latest_estimates(client):
    """The Portfolio Export sheet is one row per NON-ARCHIVED project: a
    still-maturing (Proposed) lead must appear, its estimate columns filled
    from the latest available (lead-phase) inputs; a just-created bare lead
    must appear too. The Staking Options sheet keeps its mature-leads-only
    membership, so neither lead may show there."""
    bare_pid = create_project(client, "EXPORT-LEAD-BARE")
    lead_pid = create_project(client, "EXPORT-LEAD-1")
    lead_ra_task = get_task_by_name(client, lead_pid, "Lead Resource Assessment")
    resp = client.patch(f"/api/tasks/{lead_ra_task['task_id']}/dynamic-fields",
                         json={"fields": {"lead_piip_gas_p90": "3.1",
                                          "lead_piip_gas_mean": "7.5",
                                          "lead_piip_gas_p10": "15.2"}})
    assert resp.status_code == 200
    area_task = get_task_by_name(client, lead_pid, "Reservoir Area Definition")
    resp = client.patch(f"/api/tasks/{area_task['task_id']}/dynamic-fields",
                         json={"fields": {"p90_area_km2": "2.4", "p10_area_km2": "9.8"}})
    assert resp.status_code == 200
    thickness_task = get_task_by_name(client, lead_pid, "Thickness Estimation")
    resp = client.patch(f"/api/tasks/{thickness_task['task_id']}/dynamic-fields",
                         json={"fields": {"reservoir_thickness_ft": "88"}})
    assert resp.status_code == 200

    resp = client.get("/api/export/excel")
    assert resp.status_code == 200
    workbook = openpyxl.load_workbook(io.BytesIO(resp.data))
    ws_export = workbook["Portfolio Export"]
    header = [cell.value for cell in ws_export[4]]
    by_name = {row[header.index("Well Name")]: row
               for row in ws_export.iter_rows(min_row=5, max_row=ws_export.max_row, values_only=True)}

    lead_row = by_name["EXPORT-LEAD-1"]
    assert lead_row[header.index("Status")] == "Proposed"
    assert lead_row[header.index("BP Year")] in ("", None)
    assert lead_row[header.index("OGIP P90 (BCF)")] == "3.1"
    assert lead_row[header.index("OGIP Mean (BCF)")] == "7.5"
    assert lead_row[header.index("OGIP P10 (BCF)")] == "15.2"
    assert lead_row[header.index("P90 Area (km2)")] == "2.4"
    assert lead_row[header.index("P10 Area (km2)")] == "9.8"
    # An undrilled lead has no SARH formation row: P50 Pay falls back to the
    # Thickness Estimation step's reservoir thickness estimate.
    assert lead_row[header.index("P50 Pay Thickness (ft)")] == "88"

    bare_row = by_name["EXPORT-LEAD-BARE"]
    assert bare_row[header.index("Status")] == "Proposed"
    assert bare_row[header.index("OGIP Mean (BCF)")] in ("", None)

    ws_staking = workbook["Staking Options"]
    staking_header = [cell.value for cell in ws_staking[4]]
    staking_names = {row[staking_header.index("Lead Name")]
                     for row in ws_staking.iter_rows(min_row=5, max_row=ws_staking.max_row, values_only=True)}
    assert "EXPORT-LEAD-1" not in staking_names
    assert "EXPORT-LEAD-BARE" not in staking_names
