import json
from pathlib import Path

import pytest
import yaml

from resource_engine import ConfigurationError, calculate_resources


BASE_REQUEST = {
    "scenario": "dry_gas_high_pressure",
    "method": "grv",
    "grv_p90_thousand_acre_ft": 12.6,
    "grv_p10_thousand_acre_ft": 17.3,
    "seed": 10_000,
    "iterations": 10_000,
}


def test_seed_reproducibility():
    result_a = calculate_resources(BASE_REQUEST)
    result_b = calculate_resources(BASE_REQUEST)
    assert result_a["gas_piip"] == result_b["gas_piip"]


def test_p90_p50_p10_ordering():
    result = calculate_resources(BASE_REQUEST)
    stats = result["gas_piip"]
    assert stats["p90"] < stats["p50"] < stats["p10"]


def test_recovery_factor_has_no_effect_on_piip(tmp_path):
    config = yaml.safe_load(Path("config/scenarios.yaml").read_text(encoding="utf-8"))
    modified = yaml.safe_load(Path("config/scenarios.yaml").read_text(encoding="utf-8"))
    modified["shared_parameters"]["recovery_factor_non_associated_gas"]["mean"] = 0.01
    modified["shared_parameters"]["recovery_factor_non_associated_gas"]["stddev"] = 0.001
    modified["shared_parameters"]["recovery_factor_non_associated_gas"]["p90"] = 0.008
    modified["shared_parameters"]["recovery_factor_non_associated_gas"]["p10"] = 0.012

    base_path = tmp_path / "base.yaml"
    modified_path = tmp_path / "modified.yaml"
    base_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    modified_path.write_text(yaml.safe_dump(modified), encoding="utf-8")

    assert calculate_resources(BASE_REQUEST, str(base_path))["gas_piip"] == calculate_resources(
        BASE_REQUEST, str(modified_path)
    )["gas_piip"]


def test_low_pressure_gas_lower_than_high_pressure():
    high = calculate_resources(BASE_REQUEST)
    low_request = dict(BASE_REQUEST, scenario="dry_gas_low_pressure")
    low = calculate_resources(low_request)
    assert low["gas_piip"]["mean"] < high["gas_piip"]["mean"]


def test_incomplete_area_thickness_configuration_error(tmp_path):
    config = yaml.safe_load(Path("config/scenarios.yaml").read_text(encoding="utf-8"))
    config["method_defaults"]["area_thickness"]["geometric_factor"] = {"distribution": None, "value": None}
    config["method_defaults"]["area_thickness"]["missing_fields"] = [
        "method_defaults.area_thickness.geometric_factor"
    ]
    broken_path = tmp_path / "broken_area_config.yaml"
    broken_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    request = {
        "scenario": "dry_gas_high_pressure",
        "method": "area_thickness",
        "area_p90_km2": 1.0,
        "area_p10_km2": 2.0,
        "thickness_p50_ft": 50.0,
    }
    try:
        with pytest.raises(ConfigurationError, match="geometric_factor"):
            calculate_resources(request, str(broken_path))
    finally:
        broken_path.unlink(missing_ok=True)


def test_area_thickness_calculates_with_configured_factor():
    result = calculate_resources(
        {
            "scenario": "dry_gas_high_pressure",
            "method": "area_thickness",
            "area_p90_km2": 1.0,
            "area_p10_km2": 2.0,
            "thickness_p50_ft": 50.0,
            "seed": 10_000,
            "iterations": 10_000,
        }
    )
    stats = result["gas_piip"]
    assert stats["p90"] > 0
    assert stats["p90"] < stats["p50"] < stats["p10"]
    geometric_factor = result["diagnostics"]["effective_inputs"]["geometric_factor"]
    assert geometric_factor["requested_p90"] == pytest.approx(0.4)
    assert geometric_factor["requested_p10"] == pytest.approx(0.7)
    assert geometric_factor["effective_p90"] == pytest.approx(0.4, abs=0.01)
    assert geometric_factor["effective_p10"] == pytest.approx(0.7, abs=0.01)
    assert geometric_factor["bounds"]["minimum"] == 0.0
    assert geometric_factor["bounds"]["maximum"] == 1.0
    assert geometric_factor["sampled_percentiles"]["p10"] < 1.0
    assert geometric_factor["sampled_percentiles"]["minimum"] >= 0.0
    assert geometric_factor["sampled_percentiles"]["maximum"] <= 1.0


def test_condensate_scenario_returns_gas_and_condensate():
    result = calculate_resources(dict(BASE_REQUEST, scenario="condensate_field_a"))
    assert result["resource_type"] == "condensate"
    assert result["gas_piip"]["p90"] > 0
    assert result["condensate_piip"]["p90"] > 0
    assert result["condensate_piip"]["p90"] < result["condensate_piip"]["p50"] < result["condensate_piip"]["p10"]
    assert "condensate_mmstb" in result["diagnostics"]["samples"]


def test_oil_field_c_was_removed():
    request = dict(BASE_REQUEST, scenario="oil_field_c")
    with pytest.raises(ConfigurationError, match="Unknown scenario"):
        calculate_resources(request)


def test_json_compatible_api_response():
    result = calculate_resources(BASE_REQUEST)
    json.dumps(result)
    assert result["resource_type"] == "dry_gas"
    assert result["units"]["gas"] == "BCF"
