"""Public calculation API and Monte Carlo simulation orchestration."""

from __future__ import annotations

from typing import Any

import numpy as np

from .config import load_config, resolve_shared_distribution
from .distributions import sample_distribution
from .exceptions import ConfigurationError
from .models import ResourceRequest
from .validation import require_configured_scenario, require_method_configured
from .volumetrics import area_thickness_grv_ft3, dry_gas_giip_bcf, grv_method_ft3


def calculate_resources(
    request: ResourceRequest | dict[str, Any],
    config_path: str | None = None,
) -> dict[str, Any]:
    """Calculate unrisked petroleum initially in place.

    The returned dictionary is JSON-compatible and does not require Streamlit.
    """
    normalized = ResourceRequest.from_mapping(request)
    normalized.validate()

    config = load_config(config_path)
    scenario = require_configured_scenario(config, normalized.scenario)
    require_method_configured(config, normalized.method)

    resource_type = scenario.get("resource_type")
    if resource_type not in {"dry_gas", "condensate"}:
        raise ConfigurationError("Only dry-gas and condensate scenarios are currently operational.")

    rng = np.random.default_rng(normalized.seed)
    diagnostics: dict[str, Any] = {"effective_inputs": {}, "warnings": []}

    if normalized.method == "grv":
        grv_distribution_type = config["method_defaults"]["grv"]["grv_distribution"]
        grv_samples, grv_diag = sample_distribution(
            {
                "distribution": grv_distribution_type,
                "p90": normalized.grv_p90_thousand_acre_ft,
                "p10": normalized.grv_p10_thousand_acre_ft,
            },
            normalized.iterations,
            rng,
        )
        grv_ft3 = grv_method_ft3(grv_samples)
        diagnostics["effective_inputs"]["grv_thousand_acre_ft"] = grv_diag
    elif normalized.method == "area_thickness":
        area_cfg = config["method_defaults"]["area_thickness"]
        area_samples, area_diag = sample_distribution(
            {
                "distribution": area_cfg["area_distribution"],
                "p90": normalized.area_p90_km2,
                "p10": normalized.area_p10_km2,
            },
            normalized.iterations,
            rng,
        )
        thickness_p90 = 0.60 * normalized.thickness_p50_ft
        thickness_p10 = 1.40 * normalized.thickness_p50_ft
        thickness_samples, thickness_diag = sample_distribution(
            {"distribution": "normal", "p90": thickness_p90, "p10": thickness_p10},
            normalized.iterations,
            rng,
        )
        factor_samples, factor_diag = sample_distribution(
            area_cfg["geometric_factor"],
            normalized.iterations,
            rng,
        )
        grv_ft3 = area_thickness_grv_ft3(area_samples, thickness_samples, factor_samples)
        diagnostics["effective_inputs"]["area_km2"] = area_diag
        diagnostics["effective_inputs"]["thickness_ft"] = thickness_diag
        diagnostics["effective_inputs"]["geometric_factor"] = factor_diag
    else:
        raise ConfigurationError(f"Unknown calculation method '{normalized.method}'.")

    sampled_inputs = _sample_dry_gas_inputs(config, scenario, normalized.iterations, rng)
    diagnostics["effective_inputs"].update(sampled_inputs["diagnostics"])
    diagnostics["warnings"].extend(_collect_warnings(diagnostics["effective_inputs"]))

    gas_bcf = dry_gas_giip_bcf(
        grv_ft3=grv_ft3,
        net_to_gross=sampled_inputs["samples"]["net_to_gross"],
        porosity=sampled_inputs["samples"]["porosity"],
        gas_saturation=sampled_inputs["samples"]["gas_saturation"],
        trap_fill=sampled_inputs["samples"]["trap_fill"],
        gas_expansion_factor_1_over_bg=sampled_inputs["samples"]["gas_expansion_factor_1_over_bg"],
        wet_gas_shrinkage_factor=sampled_inputs["samples"]["wet_gas_shrinkage_factor"],
    )

    stats = _petroleum_percentiles(gas_bcf)
    diagnostics["samples"] = {"gas_bcf": [float(value) for value in gas_bcf]}
    diagnostics["sample_count"] = int(gas_bcf.size)

    condensate_stats = None
    if resource_type == "condensate":
        condensate_yield = sampled_inputs["samples"]["condensate_yield"]
        condensate_mmstb = gas_bcf * condensate_yield / 1_000.0
        condensate_stats = _petroleum_percentiles(condensate_mmstb)
        diagnostics["samples"]["condensate_mmstb"] = [float(value) for value in condensate_mmstb]

    result = {
        "scenario": normalized.scenario,
        "resource_type": resource_type,
        "method": normalized.method,
        "seed": normalized.seed,
        "iterations": normalized.iterations,
        "units": {"gas": "BCF", "condensate": "MMSTB"},
        "gas_piip": stats,
        "diagnostics": diagnostics,
    }
    if condensate_stats is not None:
        result["condensate_piip"] = condensate_stats
    return result


def _sample_dry_gas_inputs(
    config: dict[str, Any],
    scenario: dict[str, Any],
    iterations: int,
    rng: np.random.Generator,
) -> dict[str, Any]:
    samples: dict[str, np.ndarray] = {}
    diagnostics: dict[str, Any] = {}
    inputs = {
        "net_to_gross": resolve_shared_distribution(config, scenario, "net_to_gross"),
        "porosity": resolve_shared_distribution(config, scenario, "porosity"),
        "gas_saturation": resolve_shared_distribution(config, scenario, "saturation"),
        "trap_fill": resolve_shared_distribution(config, scenario, "trap_fill"),
        "gas_expansion_factor_1_over_bg": scenario.get("gas_expansion_factor_1_over_bg"),
        "wet_gas_shrinkage_factor": resolve_shared_distribution(config, scenario, "shrinkage_factor"),
    }
    if scenario.get("resource_type") == "condensate":
        inputs["condensate_yield"] = scenario.get("condensate_yield")
    for name, spec in inputs.items():
        if not isinstance(spec, dict):
            raise ConfigurationError(f"Scenario input '{name}' is missing or invalid.")
        samples[name], diagnostics[name] = sample_distribution(spec, iterations, rng)
    return {"samples": samples, "diagnostics": diagnostics}


def _petroleum_percentiles(samples: np.ndarray) -> dict[str, float]:
    return {
        "p90": float(np.percentile(samples, 10)),
        "p50": float(np.percentile(samples, 50)),
        "mean": float(np.mean(samples)),
        "p10": float(np.percentile(samples, 90)),
    }


def _collect_warnings(effective_inputs: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for input_name, diagnostic in effective_inputs.items():
        for warning in diagnostic.get("warnings", []):
            warnings.append(f"{input_name}: {warning}")
    return warnings
