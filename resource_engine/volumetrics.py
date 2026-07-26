"""Petroleum volumetric formulas with explicit units."""

from __future__ import annotations

import numpy as np

from .units import km2_to_ft2, scf_to_bcf, thousand_acre_ft_to_ft3


def grv_method_ft3(grv_thousand_acre_ft: np.ndarray) -> np.ndarray:
    """Convert GRV from 10^3 acre-ft to ft^3 for direct-GRV calculations."""
    return grv_thousand_acre_ft * thousand_acre_ft_to_ft3(1.0)


def area_thickness_grv_ft3(
    area_km2: np.ndarray,
    thickness_ft: np.ndarray,
    geometric_factor: np.ndarray,
) -> np.ndarray:
    """Calculate GRV from area, thickness, and geometric factor."""
    return area_km2 * km2_to_ft2(1.0) * thickness_ft * geometric_factor


def dry_gas_giip_bcf(
    grv_ft3: np.ndarray,
    net_to_gross: np.ndarray,
    porosity: np.ndarray,
    gas_saturation: np.ndarray,
    trap_fill: np.ndarray,
    gas_expansion_factor_1_over_bg: np.ndarray,
    wet_gas_shrinkage_factor: np.ndarray,
) -> np.ndarray:
    """Calculate unrisked gas initially in place for dry gas, returned in BCF.

    GIIP_scf = GRV_ft3 * NTG * porosity * gas saturation * trap fill
        * (1/Bg) * wet-gas shrinkage factor.
    """
    giip_scf = (
        grv_ft3
        * net_to_gross
        * porosity
        * gas_saturation
        * trap_fill
        * gas_expansion_factor_1_over_bg
        * wet_gas_shrinkage_factor
    )
    return giip_scf * scf_to_bcf(1.0)
