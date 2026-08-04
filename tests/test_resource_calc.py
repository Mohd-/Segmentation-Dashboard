"""Tests for the Resource Assessment calculator (resource_calc) + its API.

resource_calc adapts the vendored resource_engine to the pop-up calculator: it
maps the dashboard's method labels/field names onto engine requests, runs the
Monte Carlo engine, and renders base64 exceedance plots. These tests pin the
request mapping, the ValueError contract (both shape errors and re-raised engine
validation), the response shape, determinism, and the GeoX benchmark parity that
travels with the engine.
"""
from __future__ import annotations

import pytest

import resource_calc as rc
from conftest import create_project, get_task_by_name


# ---------------------------------------------------------------------------
# build_request: method-label mapping
# ---------------------------------------------------------------------------

def test_build_request_grv_label_maps_to_grv_fields():
    request = rc.build_request({
        "scenario": "dry_gas_high_pressure", "method": "GRV",
        "grv_p90": "12.6", "grv_p10": "17.3",
    })
    assert request == {
        "scenario": "dry_gas_high_pressure", "method": "grv",
        "grv_p90_thousand_acre_ft": 12.6, "grv_p10_thousand_acre_ft": 17.3,
    }


def test_build_request_box_model_label_maps_to_area_thickness_fields():
    request = rc.build_request({
        "scenario": "dry_gas_high_pressure", "method": "Box Model",
        "area_p90_km2": "10", "area_p10_km2": "25", "thickness_p50_ft": "110",
    })
    assert request == {
        "scenario": "dry_gas_high_pressure", "method": "area_thickness",
        "area_p90_km2": 10.0, "area_p10_km2": 25.0, "thickness_p50_ft": 110.0,
    }


# ---------------------------------------------------------------------------
# build_request: shape-error ValueErrors (things the engine can't diagnose)
# ---------------------------------------------------------------------------

def test_build_request_blank_scenario_raises():
    with pytest.raises(ValueError, match="scenario"):
        rc.build_request({"scenario": "", "method": "GRV",
                          "grv_p90": "12.6", "grv_p10": "17.3"})


def test_build_request_unknown_method_raises():
    with pytest.raises(ValueError, match="GRV.*Box Model"):
        rc.build_request({"scenario": "dry_gas_high_pressure", "method": "grv",
                          "grv_p90": "12.6", "grv_p10": "17.3"})


@pytest.mark.parametrize("missing", ["grv_p90", "grv_p10"])
def test_build_request_missing_grv_numeric_raises(missing):
    payload = {"scenario": "dry_gas_high_pressure", "method": "GRV",
               "grv_p90": "12.6", "grv_p10": "17.3"}
    payload[missing] = ""
    with pytest.raises(ValueError, match="must be numeric"):
        rc.build_request(payload)


def test_build_request_non_numeric_area_raises():
    with pytest.raises(ValueError, match="must be numeric"):
        rc.build_request({"scenario": "dry_gas_high_pressure", "method": "Box Model",
                          "area_p90_km2": "abc", "area_p10_km2": "25", "thickness_p50_ft": "110"})


# ---------------------------------------------------------------------------
# run: engine validation errors re-raise as ValueError with the constraint
# ---------------------------------------------------------------------------

def test_run_propagates_engine_ordering_error():
    with pytest.raises(ValueError, match="P90 must be lower than"):
        rc.run({"scenario": "dry_gas_high_pressure", "method": "GRV",
                "grv_p90": "17.3", "grv_p10": "12.6"})


def test_run_propagates_engine_negative_error():
    with pytest.raises(ValueError, match="positive"):
        rc.run({"scenario": "dry_gas_high_pressure", "method": "GRV",
                "grv_p90": "-5", "grv_p10": "17.3"})


# ---------------------------------------------------------------------------
# run: response shape (dry gas vs condensate) + plot encoding
# ---------------------------------------------------------------------------

def test_run_dry_gas_shape_has_gas_no_condensate():
    out = rc.run({"scenario": "dry_gas_high_pressure", "method": "GRV",
                  "grv_p90": "12.6", "grv_p10": "17.3"})
    assert set(out["gas"]) == {"p90", "p50", "mean", "p10"}
    assert "condensate" not in out
    assert set(out["plots"]) == {"gas"}
    assert out["plots"]["gas"].startswith("data:image/png;base64,")
    assert "units" in out


def test_run_condensate_scenario_has_condensate_and_its_plot():
    out = rc.run({"scenario": "condensate_field_a", "method": "GRV",
                  "grv_p90": "12.6", "grv_p10": "17.3"})
    assert "condensate" in out
    assert set(out["plots"]) == {"gas", "condensate"}
    assert out["plots"]["condensate"].startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_run_is_deterministic():
    body = {"scenario": "dry_gas_high_pressure", "method": "GRV",
            "grv_p90": "12.6", "grv_p10": "17.3"}
    first = rc.run(dict(body))["gas"]
    second = rc.run(dict(body))["gas"]
    assert first == second


# ---------------------------------------------------------------------------
# GeoX benchmark parity (the guarantee that travels with the vendored engine)
# ---------------------------------------------------------------------------

def test_run_matches_geox_benchmark():
    out = rc.run({"scenario": "dry_gas_high_pressure", "method": "GRV",
                  "grv_p90": "12.6", "grv_p10": "17.3"})
    gas = out["gas"]
    assert gas["p90"] == pytest.approx(12.2, rel=0.05)
    assert gas["mean"] == pytest.approx(19.8, rel=0.05)
    assert gas["p10"] == pytest.approx(28.1, rel=0.05)
    # format_stored renders the display strings the frontend mirrors; each PIIP
    # is in [10, 1000) so it renders with one decimal.
    assert rc.format_stored(gas["p90"]) == f"{gas['p90']:.1f}"
    assert rc.format_stored(gas["mean"]) == f"{gas['mean']:.1f}"
    assert rc.format_stored(gas["p10"]) == f"{gas['p10']:.1f}"


# ---------------------------------------------------------------------------
# format_stored boundaries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (9.994, "9.99"),
    (10, "10.0"),
    (999.94, "999.9"),
    (1000, "1000"),
])
def test_format_stored_boundaries(value, expected):
    assert rc.format_stored(value) == expected


# ---------------------------------------------------------------------------
# scenario_options
# ---------------------------------------------------------------------------

def test_scenario_options_returns_four_configured():
    options = rc.scenario_options()
    assert len(options) == 4
    for entry in options:
        assert set(entry) == {"id", "label", "resource_type"}


# ---------------------------------------------------------------------------
# API: POST /api/tasks/<id>/resource-assessment
# ---------------------------------------------------------------------------

def test_api_resource_assessment_valid_grv(client):
    pid = create_project(client, "RESCALC-API-1")
    task = get_task_by_name(client, pid, "Lead Assessment")
    resp = client.post(f"/api/tasks/{task['task_id']}/resource-assessment", json={
        "scenario": "dry_gas_high_pressure", "method": "GRV",
        "grv_p90": "12.6", "grv_p10": "17.3",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body["gas"]) == {"p90", "p50", "mean", "p10"}
    assert body["plots"]["gas"].startswith("data:image/png;base64,")


def test_api_resource_assessment_invalid_ordering_400(client):
    pid = create_project(client, "RESCALC-API-2")
    task = get_task_by_name(client, pid, "Lead Assessment")
    resp = client.post(f"/api/tasks/{task['task_id']}/resource-assessment", json={
        "scenario": "dry_gas_high_pressure", "method": "GRV",
        "grv_p90": "17.3", "grv_p10": "12.6",
    })
    assert resp.status_code == 400
    assert "P90 must be lower than" in resp.get_json()["detail"]


def test_api_resource_assessment_nonexistent_task_404(client):
    resp = client.post("/api/tasks/999999/resource-assessment", json={
        "scenario": "dry_gas_high_pressure", "method": "GRV",
        "grv_p90": "12.6", "grv_p10": "17.3",
    })
    assert resp.status_code == 404
    assert resp.get_json()["detail"] == "Task not found"


def test_api_resource_assessment_rejects_geox_before_running_engine(client, monkeypatch):
    pid = create_project(client, "RESCALC-API-GEOX")
    task = get_task_by_name(client, pid, "Pre-Drilling GeoX Assessment")

    def engine_must_not_run(_payload):
        pytest.fail("GeoX must never invoke the in-app resource engine")

    monkeypatch.setattr(rc, "run", engine_must_not_run)
    resp = client.post(f"/api/tasks/{task['task_id']}/resource-assessment", json={})
    assert resp.status_code == 400
    assert resp.get_json()["detail"] == (
        "Resource calculator is only available for Lead Assessment."
    )
