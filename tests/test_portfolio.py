"""Tests for the WS7 Portfolio rework (reporting.get_portfolio_rows).

The Portfolio is the analysis surface for BP-enabled wells: 8 columns whose
sources each get pinned here -- gas-field derivation from the project name,
seismic AR -> block-name mapping (config.SEISMIC_BLOCK_NAMES) with raw
fallback, fluid precedence (final beats quicklook beats 'Not Drilled Yet'),
mean-OGIP precedence (post-drill -> pre-drill -> lead), and the GHEER
classification mirror.
"""
from __future__ import annotations

import json

from conftest import create_project, get_task_by_name

BP_KWARGS = {"business_plan_enabled": True, "business_plan_year": 2027}


def _rows(client, **query):
    qs = "&".join(f"{k}={v}" for k, v in query.items())
    resp = client.get("/api/portfolio/rows" + (f"?{qs}" if qs else ""))
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _row_for(client, pid):
    return next(r for r in _rows(client)["rows"] if r["project_id"] == pid)


def _save_fields(client, pid, task_name, fields):
    task = get_task_by_name(client, pid, task_name)
    resp = client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields", json={"fields": fields})
    assert resp.status_code == 200, resp.get_json()


# ---------------------------------------------------------------------------
# Gas field derivation
# ---------------------------------------------------------------------------

def test_gas_field_is_name_prefix_before_first_hyphen(client):
    pid = create_project(client, "JOHN-4", **BP_KWARGS)
    row = _row_for(client, pid)
    assert row["well_name"] == "JOHN-4"
    assert row["gas_field"] == "JOHN"


def test_gas_field_whole_name_when_no_hyphen(client):
    pid = create_project(client, "SOLO", **BP_KWARGS)
    assert _row_for(client, pid)["gas_field"] == "SOLO"


# ---------------------------------------------------------------------------
# Seismic block
# ---------------------------------------------------------------------------

def test_seismic_block_maps_last_nonempty_ar_number(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "SEISMIC_BLOCK_NAMES", {"AR-0000001": "JOHN 4"})

    pid = create_project(client, "SEISMIC-1", **BP_KWARGS)
    _save_fields(client, pid, "Reservoir CoS", {"reservoir_cos_rows": json.dumps([
        {"seismic_volume_ar_number": "AR-1111111", "reservoir_cos_pct": "40"},
        {"seismic_volume_ar_number": "", "reservoir_cos_pct": "50"},
        {"seismic_volume_ar_number": "AR-0000001", "reservoir_cos_pct": ""},
    ])})
    assert _row_for(client, pid)["seismic_block"] == "JOHN 4"


def test_seismic_block_falls_back_to_raw_ar_number(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "SEISMIC_BLOCK_NAMES", {})

    pid = create_project(client, "SEISMIC-2", **BP_KWARGS)
    _save_fields(client, pid, "Reservoir CoS", {"reservoir_cos_rows": json.dumps([
        {"seismic_volume_ar_number": "AR-9999999"},
    ])})
    assert _row_for(client, pid)["seismic_block"] == "AR-9999999"


def test_seismic_block_empty_when_no_rows(client):
    pid = create_project(client, "SEISMIC-3", **BP_KWARGS)
    assert _row_for(client, pid)["seismic_block"] == ""


# ---------------------------------------------------------------------------
# Fluid precedence
# ---------------------------------------------------------------------------

def test_fluid_defaults_to_not_drilled_yet(client):
    pid = create_project(client, "FLUID-1", **BP_KWARGS)
    assert _row_for(client, pid)["fluid"] == "Not Drilled Yet"


def test_fluid_final_beats_quicklook_beats_default(client):
    pid = create_project(client, "FLUID-2", **BP_KWARGS)
    _save_fields(client, pid, "Quicklook Logs Interpretation", {"quicklook_fluid_type": "Gas"})
    assert _row_for(client, pid)["fluid"] == "Gas"

    _save_fields(client, pid, "Final Log Analysis", {"final_fluid_type": "Dry"})
    assert _row_for(client, pid)["fluid"] == "Dry"


# ---------------------------------------------------------------------------
# Mean OGIP precedence
# ---------------------------------------------------------------------------

def test_mean_ogip_precedence_post_beats_pre_beats_lead(client):
    pid = create_project(client, "OGIP-1", **BP_KWARGS)
    _save_fields(client, pid, "Lead Resource Assessment", {"lead_piip_gas_mean": "5.0"})
    assert _row_for(client, pid)["mean_ogip"] == "5.0"

    _save_fields(client, pid, "Pre-Drilling Resource Assessment", {"pre_drill_piip_gas_mean": "7.5"})
    assert _row_for(client, pid)["mean_ogip"] == "7.5"

    _save_fields(client, pid, "Post-Drilling Resource Assessment", {"post_drill_piip_gas_mean": "9.25"})
    assert _row_for(client, pid)["mean_ogip"] == "9.25"


def test_summary_cumulative_ogip_sums_mean_ogip(client):
    pid_a = create_project(client, "SUM-A", **BP_KWARGS)
    pid_b = create_project(client, "SUM-B", **BP_KWARGS)
    _save_fields(client, pid_a, "Lead Resource Assessment", {"lead_piip_gas_mean": "4.0"})
    _save_fields(client, pid_b, "Post-Drilling Resource Assessment", {"post_drill_piip_gas_mean": "6.5"})

    payload = _rows(client)
    assert payload["summary"]["business_plan_wells"] == 2
    assert payload["summary"]["cumulative_ogip"] == 10.5


# ---------------------------------------------------------------------------
# Classification (GHEER mirror)
# ---------------------------------------------------------------------------

def test_classification_mirrors_from_gheer_save(client):
    pid = create_project(client, "CLASS-1", **BP_KWARGS)
    assert _row_for(client, pid)["classification"] == ""

    _save_fields(client, pid, "GHEER", {"gheer_classification": "Appraisal"})
    assert _row_for(client, pid)["classification"] == "Appraisal"


# ---------------------------------------------------------------------------
# Scope / filters keep working with the new row shape
# ---------------------------------------------------------------------------

def test_portfolio_scope_is_bp_enabled_only(client):
    create_project(client, "NOT-IN-PORTFOLIO")
    pid = create_project(client, "IN-PORTFOLIO", **BP_KWARGS)
    rows = _rows(client)["rows"]
    assert [r["project_id"] for r in rows] == [pid]


def test_portfolio_year_and_activity_filters(client):
    pid_2027 = create_project(client, "FILTER-2027", **BP_KWARGS)
    pid_2030 = create_project(client, "FILTER-2030", business_plan_enabled=True, business_plan_year=2030)
    client.patch(f"/api/projects/{pid_2030}/flags", json={"active_well_enabled": True})

    rows = _rows(client, year=2027)["rows"]
    assert [r["project_id"] for r in rows] == [pid_2027]

    rows = _rows(client, activity="Active")["rows"]
    assert [r["project_id"] for r in rows] == [pid_2030]

    rows = _rows(client, activity="Non-Active")["rows"]
    assert [r["project_id"] for r in rows] == [pid_2027]
