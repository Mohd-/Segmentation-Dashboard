"""Unit conversion helpers used by the volumetric formulas."""

ACRE_FT_TO_FT3 = 43_560.0
THOUSAND_ACRE_FT_TO_FT3 = 1_000.0 * ACRE_FT_TO_FT3
KM2_TO_FT2 = 10_000_000_000.0 / 929.0304
SCF_TO_BCF = 1.0 / 1_000_000_000.0


def thousand_acre_ft_to_ft3(value_thousand_acre_ft: float) -> float:
    """Convert gross rock volume from 10^3 acre-ft to cubic feet."""
    return value_thousand_acre_ft * THOUSAND_ACRE_FT_TO_FT3


def km2_to_ft2(value_km2: float) -> float:
    """Convert square kilometers to square feet.

    The constant is derived from 1 ft = 0.3048 m exactly.
    """
    return value_km2 * KM2_TO_FT2


def scf_to_bcf(value_scf: float) -> float:
    """Convert standard cubic feet to billion cubic feet."""
    return value_scf * SCF_TO_BCF
