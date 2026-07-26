"""Probabilistic PIIP assessment engine.

Vendored from the resource-assessment repo (source commit
09ba53c5ce9fcea38926b5a69806bce24ab9eee9) on 2026-07-26. Copied in as native
code so this dashboard has no pip cross-dependency on resource-assessment;
the GeoX benchmark guarantee (see tests/test_resource_engine_benchmark.py)
travels with the code.
"""

from .config import list_scenarios, load_config
from .exceptions import ConfigurationError, InputValidationError, ResourceEngineError
from .models import ResourceRequest
from .plotting import create_exceedance_figure, export_exceedance_png
from .simulation import calculate_resources

__all__ = [
    "ConfigurationError",
    "InputValidationError",
    "ResourceEngineError",
    "ResourceRequest",
    "calculate_resources",
    "create_exceedance_figure",
    "export_exceedance_png",
    "list_scenarios",
    "load_config",
]
