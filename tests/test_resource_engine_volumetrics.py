import numpy as np
import pytest

from resource_engine.volumetrics import dry_gas_giip_bcf, grv_method_ft3


def test_dry_gas_formula_deterministic_product():
    grv_ft3 = grv_method_ft3(np.array([14.9]))
    result = dry_gas_giip_bcf(
        grv_ft3=grv_ft3,
        net_to_gross=np.array([0.805]),
        porosity=np.array([0.160]),
        gas_saturation=np.array([0.806]),
        trap_fill=np.array([1.0]),
        gas_expansion_factor_1_over_bg=np.array([294.0]),
        wet_gas_shrinkage_factor=np.array([1.0]),
    )
    assert float(result[0]) == pytest.approx(19.81, rel=0.001)
