"""Validated dataclass models for engine inputs and outputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .exceptions import InputValidationError

CalculationMethod = Literal["grv", "area_thickness"]


@dataclass(frozen=True)
class ResourceRequest:
    """Public API request for a petroleum initially in place calculation."""

    scenario: str
    method: CalculationMethod
    seed: int = 10_000
    iterations: int = 10_000
    grv_p90_thousand_acre_ft: float | None = None
    grv_p10_thousand_acre_ft: float | None = None
    area_p90_km2: float | None = None
    area_p10_km2: float | None = None
    thickness_p50_ft: float | None = None
    # Per-run substitutes for the scenario's petrophysical distributions
    # (porosity, Sg, NGR, geometric factor, 1/Bg). Empty means "use the
    # scenario exactly as configured", which is what every caller that does not
    # know about them sends. See resource_engine/overrides.py.
    overrides: dict[str, Any] | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | "ResourceRequest") -> "ResourceRequest":
        """Create a request from a plain dictionary or return an existing request."""
        if isinstance(data, cls):
            return data
        return cls(**data)

    def validate(self) -> None:
        """Validate public user inputs."""
        from .overrides import validate_overrides

        # Raises with a message naming the offending parameter.
        validate_overrides(self.overrides)
        if not self.scenario:
            raise InputValidationError("A scenario id is required.")
        if self.method not in ("grv", "area_thickness"):
            raise InputValidationError("Method must be 'grv' or 'area_thickness'.")
        if not isinstance(self.seed, int) or self.seed < 0 or self.seed > 2**32 - 1:
            raise InputValidationError("Seed must be an integer between 0 and 2^32 - 1.")
        if not isinstance(self.iterations, int) or self.iterations < 100:
            raise InputValidationError("Iterations must be an integer of at least 100.")
        if self.iterations > 1_000_000:
            raise InputValidationError("Iterations must not exceed 1,000,000.")

        if self.method == "grv":
            if self.grv_p90_thousand_acre_ft is None or self.grv_p10_thousand_acre_ft is None:
                raise InputValidationError("GRV P90 and GRV P10 are required.")
            _validate_positive("GRV P90", self.grv_p90_thousand_acre_ft)
            _validate_positive("GRV P10", self.grv_p10_thousand_acre_ft)
            if self.grv_p90_thousand_acre_ft >= self.grv_p10_thousand_acre_ft:
                raise InputValidationError("GRV P90 must be lower than GRV P10.")

        if self.method == "area_thickness":
            if self.area_p90_km2 is None or self.area_p10_km2 is None or self.thickness_p50_ft is None:
                raise InputValidationError("Area P90, Area P10, and thickness P50 are required.")
            _validate_positive("Area P90", self.area_p90_km2)
            _validate_positive("Area P10", self.area_p10_km2)
            _validate_positive("Reservoir thickness P50", self.thickness_p50_ft)
            if self.area_p90_km2 >= self.area_p10_km2:
                raise InputValidationError("Area P90 must be lower than Area P10.")


@dataclass(frozen=True)
class ScenarioSummary:
    """Lightweight scenario metadata for UIs and dashboard integrations."""

    scenario_id: str
    display_name: str
    resource_type: str
    status: str
    missing_fields: tuple[str, ...]


def _validate_positive(label: str, value: float) -> None:
    if value <= 0:
        raise InputValidationError(f"{label} must be positive.")


def default_config_path() -> Path:
    """Return the default scenarios.yaml path for local project usage."""
    import config as dashboard_config

    return dashboard_config.resource_scenarios_path()
