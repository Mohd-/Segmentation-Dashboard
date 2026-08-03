"""Project-free Calculator-tab endpoint contracts."""
from __future__ import annotations

from unittest.mock import patch

import pytest


def test_project_free_resources_delegates_once_without_task_lookup(client):
    payload = {
        "scenario": "dry_gas_high_pressure", "method": "GRV",
        "grv_p90": 12.6, "grv_p10": 17.3,
    }
    result = {"gas": {"p90": 12, "mean": 20, "p10": 28}, "units": {}, "plots": {}}
    with patch("main.resource_calc.run", return_value=result) as run:
        response = client.post("/api/calculators/resources", json=payload)

    assert response.status_code == 200
    assert response.get_json() == result
    run.assert_called_once_with(payload)


def test_project_free_resources_keeps_domain_validation_as_http_400(client):
    response = client.post("/api/calculators/resources", json={
        "scenario": "dry_gas_high_pressure", "method": "GRV",
        "grv_p90": 17.3, "grv_p10": 12.6,
    })
    assert response.status_code == 400
    assert "P90 must be lower than" in response.get_json()["detail"]


def test_project_free_reservoir_cos_delegates_one_row_once(client):
    payload = {"amplitude_ratio": "0.7", "base_tight_sarah": "0.5", "pull_up": "Yes"}
    with patch("main.cos.calculate_reservoir_cos_rows",
               return_value='[{"amplitude_ratio":"0.7","reservoir_cos_pct":"80"}]') as calculate:
        response = client.post("/api/calculators/reservoir-cos", json=payload)

    assert response.status_code == 200
    assert response.get_json() == [{"amplitude_ratio": "0.7", "reservoir_cos_pct": "80"}]
    calculate.assert_called_once_with([payload])


def test_project_free_reservoir_cos_runs_approved_model(client):
    response = client.post("/api/calculators/reservoir-cos", json={
        "amplitude_ratio": "0.9", "base_tight_sarah": "0.9", "pull_up": "Yes",
    })
    assert response.status_code == 200
    body = response.get_json()
    assert isinstance(body, list) and len(body) == 1
    assert body[0]["pull_up"] == "Yes"
    assert body[0]["reservoir_cos_pct"].isdigit()


@pytest.mark.parametrize("path", [
    "/api/calculators/resources",
    "/api/calculators/reservoir-cos",
])
def test_calculator_endpoints_reject_empty_or_malformed_json_body(client, path):
    empty = client.post(path)
    assert empty.status_code == 400
    assert empty.get_json()["detail"] == "Request body must be a JSON object."

    malformed = client.post(path, data="{", content_type="application/json")
    assert malformed.status_code == 400
    assert malformed.get_json()["detail"] == "Request body must be a JSON object."


def test_project_free_resources_rejects_non_object_before_domain_call(client):
    with patch("main.resource_calc.run") as run:
        response = client.post("/api/calculators/resources", json=["not", "an", "object"])
    assert response.status_code == 400
    assert response.get_json()["detail"] == "Request body must be a JSON object."
    run.assert_not_called()


def test_project_free_reservoir_rejects_non_object_before_domain_call(client):
    with patch("main.cos.calculate_reservoir_cos_rows") as calculate:
        response = client.post("/api/calculators/reservoir-cos", json="not an object")
    assert response.status_code == 400
    assert response.get_json()["detail"] == "Request body must be a JSON object."
    calculate.assert_not_called()


def test_project_free_reservoir_rejects_invalid_pull_up(client):
    response = client.post("/api/calculators/reservoir-cos", json={
        "amplitude_ratio": "0.9", "base_tight_sarah": "0.9", "pull_up": "maybe",
    })
    assert response.status_code == 400
    assert "No, Semi, or Yes" in response.get_json()["detail"]
