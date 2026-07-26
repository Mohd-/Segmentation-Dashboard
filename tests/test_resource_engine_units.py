from resource_engine.units import km2_to_ft2, scf_to_bcf, thousand_acre_ft_to_ft3


def test_acre_ft_to_ft3_conversion():
    assert thousand_acre_ft_to_ft3(12.6) == 12.6 * 1000 * 43_560


def test_km2_to_ft2_conversion():
    assert km2_to_ft2(1.0) == 10_000_000_000.0 / 929.0304


def test_scf_to_bcf_conversion():
    assert scf_to_bcf(1_000_000_000) == 1.0
