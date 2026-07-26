import pytest

from resource_engine import calculate_resources


def test_high_pressure_benchmark():
    result = calculate_resources(
        {
            "scenario": "dry_gas_high_pressure",
            "method": "grv",
            "grv_p90_thousand_acre_ft": 12.6,
            "grv_p10_thousand_acre_ft": 17.3,
            "seed": 10_000,
            "iterations": 10_000,
        }
    )
    expected = {"p90": 12.2, "p50": 19.2, "mean": 19.8, "p10": 28.1}
    for key, value in expected.items():
        assert result["gas_piip"][key] == pytest.approx(value, rel=0.05)
