import numpy as np
import pytest
from scipy.stats import norm

from resource_engine import ConfigurationError
from resource_engine.distributions import (
    ORDINARY_CDF_FOR_P1,
    distribution_parameters,
    lognormal_mu_sigma_from_p90_p10,
    normal_mean_stddev_from_p90_p10,
    sample_distribution,
)


def test_constant_distribution():
    rng = np.random.default_rng(1)
    samples, diagnostics = sample_distribution({"distribution": "constant", "value": 7.5}, 100, rng)
    assert np.all(samples == 7.5)
    assert diagnostics["bounds"]["minimum"] == 0.0
    assert diagnostics["sampled_percentiles"]["p50"] == 7.5


def test_negative_constant_fails_default_minimum():
    rng = np.random.default_rng(1)
    with pytest.raises(ConfigurationError, match="below its configured minimum"):
        sample_distribution({"distribution": "constant", "value": -1.0}, 100, rng)


def test_normal_sampling():
    rng = np.random.default_rng(123)
    samples, _ = sample_distribution({"distribution": "normal", "mean": 10.0, "stddev": 2.0}, 20_000, rng)
    assert samples.mean() == pytest.approx(10.0, rel=0.03)


def test_default_minimum_prevents_negative_samples():
    rng = np.random.default_rng(123)
    samples, diagnostics = sample_distribution({"distribution": "normal", "mean": 1.0, "stddev": 2.0}, 20_000, rng)
    assert samples.min() >= 0.0
    assert diagnostics["bounds"]["minimum"] == 0.0


def test_negative_configured_minimum_is_raised_to_zero():
    rng = np.random.default_rng(123)
    samples, diagnostics = sample_distribution(
        {"distribution": "normal", "mean": 1.0, "stddev": 2.0, "minimum": -5.0},
        20_000,
        rng,
    )
    assert samples.min() >= 0.0
    assert diagnostics["bounds"]["minimum"] == 0.0


def test_lognormal_sampling():
    rng = np.random.default_rng(123)
    samples, _ = sample_distribution({"distribution": "lognormal", "p90": 10.0, "p10": 20.0}, 20_000, rng)
    assert np.percentile(samples, 10) == pytest.approx(10.0, rel=0.05)
    assert np.percentile(samples, 90) == pytest.approx(20.0, rel=0.05)


def test_geox_style_effective_values_for_capped_normal():
    rng = np.random.default_rng(10_000)
    _, diagnostics = sample_distribution(
        {"distribution": "normal", "p90": 0.674, "p10": 0.96, "minimum": 0.0, "maximum": 0.96},
        10_000,
        rng,
    )
    assert diagnostics["requested_p90"] == pytest.approx(0.674)
    assert diagnostics["requested_p10"] == pytest.approx(0.96)
    assert diagnostics["effective_p90"] == pytest.approx(0.668, abs=0.01)
    assert diagnostics["effective_p50"] == pytest.approx(0.803, abs=0.01)
    assert diagnostics["effective_p10"] == pytest.approx(0.915, abs=0.01)


def test_geox_style_effective_values_for_wide_lognormal():
    rng = np.random.default_rng(10_000)
    _, diagnostics = sample_distribution(
        {"distribution": "lognormal", "p90": 2.0, "p10": 200.0, "minimum": 0.0},
        10_000,
        rng,
    )
    assert diagnostics["requested_p90"] == pytest.approx(2.0)
    assert diagnostics["requested_p10"] == pytest.approx(200.0)
    assert diagnostics["effective_p90"] == pytest.approx(2.08, abs=0.2)
    assert diagnostics["effective_p50"] == pytest.approx(20.0, abs=1.0)
    assert diagnostics["effective_p10"] == pytest.approx(192.1, abs=15.0)


def test_p90_p10_conversion():
    mean, stddev = normal_mean_stddev_from_p90_p10(80.0, 120.0)
    assert mean == pytest.approx(100.0)
    assert mean + stddev * norm.ppf(0.9) == pytest.approx(120.0)

    mu, sigma = lognormal_mu_sigma_from_p90_p10(10.0, 20.0)
    assert np.exp(mu + sigma * norm.ppf(0.1)) == pytest.approx(10.0)
    assert np.exp(mu + sigma * norm.ppf(0.9)) == pytest.approx(20.0)


def test_upper_p1_cap_adjustment():
    params = distribution_parameters(
        {
            "distribution": "normal",
            "p90": 0.674,
            "p10": 0.94,
            "maximum": 0.96,
            "cap_strategy": "geox_tail_cap",
        }
    )
    implied_p1 = params.mean + params.stddev * norm.ppf(ORDINARY_CDF_FOR_P1)
    assert implied_p1 == pytest.approx(0.96)
    assert params.effective_p10 < 0.94
    assert params.effective_p90 == pytest.approx(0.674)


def test_lower_p99_cap_adjustment():
    params = distribution_parameters(
        {
            "distribution": "normal",
            "p90": 0.02,
            "p10": 0.30,
            "minimum": 0.0,
            "cap_strategy": "geox_tail_cap",
        }
    )
    implied_p99 = params.mean + params.stddev * norm.ppf(0.01)
    assert implied_p99 == pytest.approx(0.0)
    assert params.effective_p90 > 0.02
    assert params.effective_p10 == pytest.approx(0.30)


def test_physical_bound_enforcement():
    rng = np.random.default_rng(5)
    samples, _ = sample_distribution(
        {
            "distribution": "normal",
            "mean": 0.8,
            "stddev": 0.3,
            "minimum": 0.0,
            "maximum": 0.96,
            "cap_strategy": "geox_tail_cap",
            "p90": 0.42,
            "p10": 1.18,
        },
        5_000,
        rng,
    )
    assert samples.min() >= 0.0
    assert samples.max() <= 0.96
    assert np.count_nonzero(samples == 0.96) == 0
