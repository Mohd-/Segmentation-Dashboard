"""Shared validation helpers for scenarios and calculation methods."""

from __future__ import annotations

from typing import Any

from .exceptions import ConfigurationError


def require_configured_scenario(config: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    """Return a configured scenario or raise a clear configuration error."""
    scenarios = config.get("scenarios", {})
    scenario = scenarios.get(scenario_id)
    if scenario is None:
        raise ConfigurationError(f"Unknown scenario '{scenario_id}'.")
    if scenario.get("status") != "configured":
        missing = scenario.get("missing_fields", [])
        details = ", ".join(missing) if missing else "scenario status is pending"
        raise ConfigurationError(f"Scenario '{scenario_id}' is not configured. Missing: {details}.")
    return scenario


def require_method_configured(config: dict[str, Any], method: str) -> None:
    """Raise if a calculation method has missing technical configuration."""
    if method == "grv":
        return
    if method == "area_thickness":
        area_cfg = config.get("method_defaults", {}).get("area_thickness", {})
        missing = list(area_cfg.get("missing_fields") or [])
        if area_cfg.get("area_distribution") is None and "method_defaults.area_thickness.area_distribution" not in missing:
            missing.append("method_defaults.area_thickness.area_distribution")
        geometric_factor = area_cfg.get("geometric_factor")
        if not geometric_factor or geometric_factor.get("distribution") is None:
            if "method_defaults.area_thickness.geometric_factor" not in missing:
                missing.append("method_defaults.area_thickness.geometric_factor")
        if missing:
            raise ConfigurationError(
                "Area x Thickness is configuration pending. Missing: " + ", ".join(missing) + "."
            )
        return
    raise ConfigurationError(f"Unknown method '{method}'.")


def validate_config(config: dict[str, Any]) -> list[str]:
    """Validate startup configuration and return non-fatal pending-field messages."""
    errors: list[str] = []
    if "shared_parameters" not in config:
        errors.append("Missing shared_parameters block.")
    if "scenarios" not in config:
        errors.append("Missing scenarios block.")
    if errors:
        raise ConfigurationError("; ".join(errors))

    pending_messages: list[str] = []
    for scenario_id, scenario in config.get("scenarios", {}).items():
        if scenario.get("status") == "pending":
            missing = scenario.get("missing_fields", [])
            pending_messages.append(f"{scenario_id}: {', '.join(missing)}")
        elif scenario.get("status") != "configured":
            errors.append(f"{scenario_id}: status must be configured or pending.")
    if errors:
        raise ConfigurationError("; ".join(errors))
    return pending_messages
