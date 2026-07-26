"""Scenario configuration loading and resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .exceptions import ConfigurationError
from .models import ScenarioSummary, default_config_path
from .validation import validate_config


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate the YAML scenario configuration."""
    config_path = Path(path) if path is not None else default_config_path()
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Configuration file not found: {config_path}") from exc
    if not isinstance(config, dict):
        raise ConfigurationError("Configuration file must contain a YAML mapping.")
    validate_config(config)
    return config


def list_scenarios(path: str | Path | None = None) -> list[ScenarioSummary]:
    """Return scenario cards metadata for UIs or dashboard integrations."""
    config = load_config(path)
    summaries: list[ScenarioSummary] = []
    for scenario_id, scenario in config["scenarios"].items():
        summaries.append(
            ScenarioSummary(
                scenario_id=scenario_id,
                display_name=str(scenario.get("display_name", scenario_id)),
                resource_type=str(scenario.get("resource_type", "")),
                status=str(scenario.get("status", "pending")),
                missing_fields=tuple(scenario.get("missing_fields") or ()),
            )
        )
    return summaries


def resolve_shared_distribution(config: dict[str, Any], scenario: dict[str, Any], name: str) -> dict[str, Any]:
    """Resolve a scenario shared-parameter reference into a distribution config."""
    shared_key = scenario.get("shared", {}).get(name)
    if not shared_key:
        raise ConfigurationError(f"Scenario missing shared parameter reference '{name}'.")
    shared_parameters = config.get("shared_parameters", {})
    distribution = shared_parameters.get(shared_key)
    if distribution is None:
        raise ConfigurationError(f"Shared parameter '{shared_key}' was not found.")
    if not isinstance(distribution, dict):
        raise ConfigurationError(f"Shared parameter '{shared_key}' must be a mapping.")
    return distribution
