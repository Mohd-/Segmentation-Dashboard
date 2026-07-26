"""Distribution parameterization, cap diagnostics, and random sampling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.stats import norm, truncnorm

from .exceptions import ConfigurationError

ORDINARY_CDF_FOR_P90 = 0.10
ORDINARY_CDF_FOR_P50 = 0.50
ORDINARY_CDF_FOR_P10 = 0.90
ORDINARY_CDF_FOR_P99 = 0.01
ORDINARY_CDF_FOR_P1 = 0.99
DEFAULT_MINIMUM = 0.0


@dataclass(frozen=True)
class DistributionParameters:
    """Numerical distribution parameters plus developer diagnostics."""

    distribution: str
    mean: float
    stddev: float
    p90: float
    p50: float
    p10: float
    requested_p90: float | None = None
    requested_p10: float | None = None
    effective_p90: float | None = None
    effective_p10: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    warnings: tuple[str, ...] = ()


def sample_distribution(
    spec: dict[str, Any],
    size: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Sample a configured constant, normal, or lognormal distribution.

    Physical bounds are enforced with bounded sampling, not blind clipping, so the
    returned sample has no artificial pile-up at the limits.
    """
    distribution = _distribution_name(spec)
    if distribution == "constant":
        value = _require_number(spec, "value")
        _validate_bounds(value, spec)
        samples = np.full(size, value, dtype=float)
        return samples, _diagnostics_for_constant(value, spec, samples)

    params = distribution_parameters(spec)
    sampling_spec = dict(spec)
    if not sampling_spec.get("apply_cap_adjustment_to_sampling", False):
        sampling_spec.pop("cap_strategy", None)
    sampling_params = distribution_parameters(sampling_spec)
    if distribution == "normal":
        samples = _sample_bounded_normal(
            sampling_params.mean,
            sampling_params.stddev,
            size,
            rng,
            sampling_params.minimum,
            sampling_params.maximum,
        )
    elif distribution == "lognormal":
        mu, sigma = lognormal_mu_sigma_from_p90_p10(sampling_params.p90, sampling_params.p10)
        samples = _sample_bounded_lognormal(mu, sigma, size, rng, sampling_params.minimum, sampling_params.maximum)
    else:
        raise ConfigurationError(f"Unsupported distribution '{distribution}'.")

    diagnostics = _diagnostics_from_params(params, samples)
    return samples, diagnostics


def distribution_parameters(spec: dict[str, Any]) -> DistributionParameters:
    """Convert a distribution config into mean/stddev and petroleum percentiles."""
    distribution = _distribution_name(spec)
    minimum, maximum = _effective_bounds(spec)
    warnings: list[str] = []

    if distribution == "constant":
        value = _require_number(spec, "value")
        return DistributionParameters(
            distribution="constant",
            mean=value,
            stddev=0.0,
            p90=value,
            p50=value,
            p10=value,
            requested_p90=value,
            requested_p10=value,
            effective_p90=value,
            effective_p10=value,
            minimum=minimum,
            maximum=maximum,
        )

    if distribution == "normal":
        if "mean" in spec and "stddev" in spec:
            mean = _require_number(spec, "mean")
            stddev = _require_number(spec, "stddev")
            p90 = _optional_number(spec, "p90")
            p10 = _optional_number(spec, "p10")
            if p90 is None:
                p90 = normal_value_at_cdf(mean, stddev, ORDINARY_CDF_FOR_P90)
            if p10 is None:
                p10 = normal_value_at_cdf(mean, stddev, ORDINARY_CDF_FOR_P10)
        else:
            p90 = _require_number(spec, "p90")
            p10 = _require_number(spec, "p10")
            mean, stddev = normal_mean_stddev_from_p90_p10(p90, p10)
        _validate_positive_stddev(stddev, spec)
        params = DistributionParameters(
            distribution="normal",
            mean=mean,
            stddev=stddev,
            p90=p90,
            p50=mean,
            p10=p10,
            requested_p90=p90,
            requested_p10=p10,
            effective_p90=p90,
            effective_p10=p10,
            minimum=minimum,
            maximum=maximum,
        )
        params = apply_geox_tail_cap(params, spec)
        return params if params.warnings else params

    if distribution == "lognormal":
        p90 = _require_number(spec, "p90")
        p10 = _require_number(spec, "p10")
        mu, sigma = lognormal_mu_sigma_from_p90_p10(p90, p10)
        _validate_positive_stddev(sigma, spec)
        mean = float(np.exp(mu + 0.5 * sigma * sigma))
        p50 = float(np.exp(mu))
        params = DistributionParameters(
            distribution="lognormal",
            mean=mean,
            stddev=float(np.sqrt((np.exp(sigma * sigma) - 1.0) * np.exp(2.0 * mu + sigma * sigma))),
            p90=p90,
            p50=p50,
            p10=p10,
            requested_p90=p90,
            requested_p10=p10,
            effective_p90=p90,
            effective_p10=p10,
            minimum=minimum,
            maximum=maximum,
        )
        return apply_geox_tail_cap(params, spec)

    raise ConfigurationError(f"Unsupported distribution '{distribution}'.")


def apply_geox_tail_cap(params: DistributionParameters, spec: dict[str, Any]) -> DistributionParameters:
    """Return cap-adjusted effective percentiles using the GeoX-style rule.

    The requested percentiles are preserved in diagnostics. The effective values
    show how P10 or P90 would move so P1/P99 lands exactly on a configured cap.
    """
    if spec.get("cap_strategy") != "geox_tail_cap":
        return params

    p90 = params.p90
    p10 = params.p10
    mean = params.mean
    stddev = params.stddev
    warnings = list(params.warnings)

    if params.distribution == "normal":
        if params.maximum is not None:
            implied_p1 = normal_value_at_cdf(mean, stddev, ORDINARY_CDF_FOR_P1)
            if implied_p1 > params.maximum:
                mean, stddev, p10 = _normal_preserve_p90_with_upper_p1(p90, params.maximum)
                warnings.append(
                    f"Upper P1 cap adjusted effective P10 from {params.p10:.6g} to {p10:.6g}."
                )
        if params.minimum is not None:
            implied_p99 = normal_value_at_cdf(mean, stddev, ORDINARY_CDF_FOR_P99)
            if implied_p99 < params.minimum:
                mean, stddev, p90 = _normal_preserve_p10_with_lower_p99(p10, params.minimum)
                warnings.append(
                    f"Lower P99 cap adjusted effective P90 from {params.p90:.6g} to {p90:.6g}."
                )
        return DistributionParameters(
            distribution=params.distribution,
            mean=mean,
            stddev=stddev,
            p90=p90,
            p50=mean,
            p10=p10,
            requested_p90=params.requested_p90,
            requested_p10=params.requested_p10,
            effective_p90=p90,
            effective_p10=p10,
            minimum=params.minimum,
            maximum=params.maximum,
            warnings=tuple(warnings),
        )

    if params.distribution == "lognormal":
        mu, sigma = lognormal_mu_sigma_from_p90_p10(p90, p10)
        if params.maximum is not None:
            implied_p1 = float(np.exp(mu + sigma * norm.ppf(ORDINARY_CDF_FOR_P1)))
            if implied_p1 > params.maximum:
                mu, sigma, p10 = _lognormal_preserve_p90_with_upper_p1(p90, params.maximum)
                warnings.append(
                    f"Upper P1 cap adjusted effective P10 from {params.p10:.6g} to {p10:.6g}."
                )
        if params.minimum is not None and params.minimum > 0:
            implied_p99 = float(np.exp(mu + sigma * norm.ppf(ORDINARY_CDF_FOR_P99)))
            if implied_p99 < params.minimum:
                mu, sigma, p90 = _lognormal_preserve_p10_with_lower_p99(p10, params.minimum)
                warnings.append(
                    f"Lower P99 cap adjusted effective P90 from {params.p90:.6g} to {p90:.6g}."
                )
        mean = float(np.exp(mu + 0.5 * sigma * sigma))
        p50 = float(np.exp(mu))
        stddev = float(np.sqrt((np.exp(sigma * sigma) - 1.0) * np.exp(2.0 * mu + sigma * sigma)))
        return DistributionParameters(
            distribution=params.distribution,
            mean=mean,
            stddev=stddev,
            p90=p90,
            p50=p50,
            p10=p10,
            requested_p90=params.requested_p90,
            requested_p10=params.requested_p10,
            effective_p90=p90,
            effective_p10=p10,
            minimum=params.minimum,
            maximum=params.maximum,
            warnings=tuple(warnings),
        )

    return params


def normal_mean_stddev_from_p90_p10(p90: float, p10: float) -> tuple[float, float]:
    """Return normal mean and standard deviation from petroleum P90/P10 values."""
    if p90 <= 0 or p10 <= 0 or p90 >= p10:
        raise ConfigurationError("P90 must be positive and lower than P10.")
    z90 = norm.ppf(ORDINARY_CDF_FOR_P10)
    mean = (p90 + p10) / 2.0
    stddev = (p10 - p90) / (2.0 * z90)
    return float(mean), float(stddev)


def lognormal_mu_sigma_from_p90_p10(p90: float, p10: float) -> tuple[float, float]:
    """Return log-space mu and sigma from petroleum P90/P10 values."""
    if p90 <= 0 or p10 <= 0 or p90 >= p10:
        raise ConfigurationError("Lognormal P90 must be positive and lower than P10.")
    z90 = norm.ppf(ORDINARY_CDF_FOR_P10)
    mu = (np.log(p90) + np.log(p10)) / 2.0
    sigma = (np.log(p10) - np.log(p90)) / (2.0 * z90)
    return float(mu), float(sigma)


def normal_value_at_cdf(mean: float, stddev: float, cdf: float) -> float:
    """Return a normal quantile for an ordinary CDF probability."""
    return float(mean + stddev * norm.ppf(cdf))


def _normal_preserve_p90_with_upper_p1(p90: float, upper_p1: float) -> tuple[float, float, float]:
    stddev = (upper_p1 - p90) / (norm.ppf(ORDINARY_CDF_FOR_P1) - norm.ppf(ORDINARY_CDF_FOR_P90))
    mean = p90 - stddev * norm.ppf(ORDINARY_CDF_FOR_P90)
    p10 = normal_value_at_cdf(mean, stddev, ORDINARY_CDF_FOR_P10)
    return float(mean), float(stddev), float(p10)


def _normal_preserve_p10_with_lower_p99(p10: float, lower_p99: float) -> tuple[float, float, float]:
    stddev = (p10 - lower_p99) / (norm.ppf(ORDINARY_CDF_FOR_P10) - norm.ppf(ORDINARY_CDF_FOR_P99))
    mean = p10 - stddev * norm.ppf(ORDINARY_CDF_FOR_P10)
    p90 = normal_value_at_cdf(mean, stddev, ORDINARY_CDF_FOR_P90)
    return float(mean), float(stddev), float(p90)


def _lognormal_preserve_p90_with_upper_p1(p90: float, upper_p1: float) -> tuple[float, float, float]:
    sigma = (np.log(upper_p1) - np.log(p90)) / (
        norm.ppf(ORDINARY_CDF_FOR_P1) - norm.ppf(ORDINARY_CDF_FOR_P90)
    )
    mu = np.log(p90) - sigma * norm.ppf(ORDINARY_CDF_FOR_P90)
    p10 = float(np.exp(mu + sigma * norm.ppf(ORDINARY_CDF_FOR_P10)))
    return float(mu), float(sigma), p10


def _lognormal_preserve_p10_with_lower_p99(p10: float, lower_p99: float) -> tuple[float, float, float]:
    sigma = (np.log(p10) - np.log(lower_p99)) / (
        norm.ppf(ORDINARY_CDF_FOR_P10) - norm.ppf(ORDINARY_CDF_FOR_P99)
    )
    mu = np.log(p10) - sigma * norm.ppf(ORDINARY_CDF_FOR_P10)
    p90 = float(np.exp(mu + sigma * norm.ppf(ORDINARY_CDF_FOR_P90)))
    return float(mu), float(sigma), p90


def _sample_bounded_normal(
    mean: float,
    stddev: float,
    size: int,
    rng: np.random.Generator,
    minimum: float | None,
    maximum: float | None,
) -> np.ndarray:
    if minimum is None and maximum is None:
        return rng.normal(mean, stddev, size=size)
    a = -np.inf if minimum is None else (minimum - mean) / stddev
    b = np.inf if maximum is None else (maximum - mean) / stddev
    return truncnorm.rvs(a, b, loc=mean, scale=stddev, size=size, random_state=rng)


def _sample_bounded_lognormal(
    mu: float,
    sigma: float,
    size: int,
    rng: np.random.Generator,
    minimum: float | None,
    maximum: float | None,
) -> np.ndarray:
    if minimum is not None and minimum < 0:
        raise ConfigurationError("Lognormal minimum cannot be negative.")
    log_min = -np.inf if minimum is None or minimum == 0 else np.log(minimum)
    log_max = np.inf if maximum is None else np.log(maximum)
    if np.isneginf(log_min) and np.isposinf(log_max):
        return rng.lognormal(mu, sigma, size=size)
    normal_samples = truncnorm.rvs(
        (log_min - mu) / sigma,
        (log_max - mu) / sigma,
        loc=mu,
        scale=sigma,
        size=size,
        random_state=rng,
    )
    return np.exp(normal_samples)


def _distribution_name(spec: dict[str, Any]) -> str:
    distribution = spec.get("distribution")
    if not isinstance(distribution, str):
        raise ConfigurationError("Distribution config requires a string 'distribution' field.")
    return distribution.lower()


def _require_number(spec: dict[str, Any], key: str) -> float:
    value = spec.get(key)
    if value is None:
        raise ConfigurationError(f"Distribution config missing '{key}'.")
    if not isinstance(value, (int, float)):
        raise ConfigurationError(f"Distribution field '{key}' must be numeric.")
    return float(value)


def _optional_number(spec: dict[str, Any], key: str) -> float | None:
    value = spec.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise ConfigurationError(f"Distribution field '{key}' must be numeric when provided.")
    return float(value)


def _validate_positive_stddev(stddev: float, spec: dict[str, Any]) -> None:
    if stddev <= 0:
        raise ConfigurationError(f"{spec.get('distribution')} distribution stddev/sigma must be positive.")


def _validate_bounds(value: float, spec: dict[str, Any]) -> None:
    minimum, maximum = _effective_bounds(spec)
    if minimum is not None and value < minimum:
        raise ConfigurationError("Constant value is below its configured minimum.")
    if maximum is not None and value > maximum:
        raise ConfigurationError("Constant value is above its configured maximum.")


def _effective_bounds(spec: dict[str, Any]) -> tuple[float, float | None]:
    minimum = _optional_number(spec, "minimum")
    maximum = _optional_number(spec, "maximum")
    if minimum is None or minimum < DEFAULT_MINIMUM:
        minimum = DEFAULT_MINIMUM
    if maximum is not None and maximum < minimum:
        raise ConfigurationError("Distribution maximum cannot be below the effective minimum.")
    return minimum, maximum


def _diagnostics_for_constant(value: float, spec: dict[str, Any], samples: np.ndarray) -> dict[str, Any]:
    minimum, maximum = _effective_bounds(spec)
    return {
        "distribution": "constant",
        "mean": value,
        "stddev": 0.0,
        "requested_p90": value,
        "requested_p50": value,
        "requested_p10": value,
        "effective_p90": value,
        "effective_p50": value,
        "effective_p10": value,
        "bounds": {"minimum": minimum, "maximum": maximum},
        "sampled_percentiles": _sampled_percentiles(samples),
        "warnings": [],
    }


def _diagnostics_from_params(params: DistributionParameters, samples: np.ndarray) -> dict[str, Any]:
    sampled_percentiles = _sampled_percentiles(samples)
    return {
        "distribution": params.distribution,
        "mean": float(np.mean(samples)),
        "stddev": float(np.std(samples, ddof=0)),
        "fitted_mean": params.mean,
        "fitted_stddev": params.stddev,
        "requested_p90": params.requested_p90,
        "requested_p50": params.p50,
        "requested_p10": params.requested_p10,
        "effective_p90": sampled_percentiles["p90"],
        "effective_p50": sampled_percentiles["p50"],
        "effective_p10": sampled_percentiles["p10"],
        "bounds": {"minimum": params.minimum, "maximum": params.maximum},
        "sampled_percentiles": sampled_percentiles,
        "warnings": list(params.warnings),
    }


def _sampled_percentiles(samples: np.ndarray) -> dict[str, float]:
    return {
        "minimum": float(np.min(samples)),
        "p90": float(np.percentile(samples, 10)),
        "p50": float(np.percentile(samples, 50)),
        "p10": float(np.percentile(samples, 90)),
        "maximum": float(np.max(samples)),
    }
