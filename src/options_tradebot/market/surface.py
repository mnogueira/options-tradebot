"""Volatility surface calibration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt
from typing import Sequence

import numpy as np
from scipy.optimize import minimize

from options_tradebot.market.models import OptionSnapshot, UnderlyingType


@dataclass(frozen=True, slots=True)
class SurfaceCalibrationResult:
    """Diagnostics from the fitted surface."""

    observations: int
    weighted_r_squared: float
    coefficients: tuple[float, ...]
    model_name: str


class LiquidityWeightedVolSurface:
    """Smooth weighted-regression surface fitted on log-moneyness and maturity."""

    def __init__(self, coefficients: np.ndarray, reference_rate: float):
        self._coefficients = np.asarray(coefficients, dtype=float)
        self.reference_rate = float(reference_rate)

    def volatility(
        self,
        *,
        spot: float,
        strike: float,
        time_to_expiry: float,
        dividend_yield: float = 0.0,
        underlying_type: UnderlyingType = UnderlyingType.SPOT,
    ) -> float:
        if time_to_expiry <= 0:
            return 0.0
        forward = (
            spot
            if underlying_type == UnderlyingType.FUTURE
            else spot * np.exp((self.reference_rate - dividend_yield) * time_to_expiry)
        )
        moneyness = log(max(strike, 1e-8) / max(forward, 1e-8))
        root_t = sqrt(time_to_expiry)
        features = np.asarray(
            [
                1.0,
                moneyness,
                moneyness * moneyness,
                time_to_expiry,
                root_t,
                moneyness * root_t,
            ],
            dtype=float,
        )
        fitted = float(features @ self._coefficients)
        return max(fitted, 0.05)

    def volatility_for_snapshot(self, snapshot: OptionSnapshot) -> float:
        return self.volatility(
            spot=snapshot.underlying_price,
            strike=snapshot.contract.strike,
            time_to_expiry=snapshot.time_to_expiry,
            dividend_yield=snapshot.dividend_yield,
            underlying_type=snapshot.contract.underlying_type,
        )


@dataclass(frozen=True, slots=True)
class SVISliceParameters:
    """One calibrated SVI smile slice."""

    time_to_expiry: float
    a: float
    b: float
    rho: float
    m: float
    sigma: float

    def total_variance(self, moneyness: float) -> float:
        raw = self.a + self.b * (
            self.rho * (moneyness - self.m) + sqrt((moneyness - self.m) ** 2 + self.sigma**2)
        )
        return max(raw, 1e-8)


class SVIVolSurface:
    """SVI slices interpolated across maturity with a regression fallback."""

    def __init__(
        self,
        slices: Sequence[SVISliceParameters],
        *,
        fallback_surface: LiquidityWeightedVolSurface,
        reference_rate: float,
    ):
        self._slices = tuple(sorted(slices, key=lambda item: item.time_to_expiry))
        self._fallback_surface = fallback_surface
        self.reference_rate = float(reference_rate)

    def volatility(
        self,
        *,
        spot: float,
        strike: float,
        time_to_expiry: float,
        dividend_yield: float = 0.0,
        underlying_type: UnderlyingType = UnderlyingType.SPOT,
    ) -> float:
        if time_to_expiry <= 0 or not self._slices:
            return self._fallback_surface.volatility(
                spot=spot,
                strike=strike,
                time_to_expiry=time_to_expiry,
                dividend_yield=dividend_yield,
                underlying_type=underlying_type,
            )
        forward = (
            spot
            if underlying_type == UnderlyingType.FUTURE
            else spot * np.exp((self.reference_rate - dividend_yield) * time_to_expiry)
        )
        moneyness = log(max(strike, 1e-8) / max(forward, 1e-8))
        total_variance = self._interpolated_total_variance(moneyness, time_to_expiry)
        return max(sqrt(total_variance / max(time_to_expiry, 1e-8)), 0.05)

    def volatility_for_snapshot(self, snapshot: OptionSnapshot) -> float:
        return self.volatility(
            spot=snapshot.underlying_price,
            strike=snapshot.contract.strike,
            time_to_expiry=snapshot.time_to_expiry,
            dividend_yield=snapshot.dividend_yield,
            underlying_type=snapshot.contract.underlying_type,
        )

    def _interpolated_total_variance(self, moneyness: float, time_to_expiry: float) -> float:
        if len(self._slices) == 1:
            return self._slices[0].total_variance(moneyness)
        if time_to_expiry <= self._slices[0].time_to_expiry:
            return self._slices[0].total_variance(moneyness)
        if time_to_expiry >= self._slices[-1].time_to_expiry:
            return self._slices[-1].total_variance(moneyness)
        for lower, upper in zip(self._slices, self._slices[1:]):
            if lower.time_to_expiry <= time_to_expiry <= upper.time_to_expiry:
                lower_total = lower.total_variance(moneyness)
                upper_total = upper.total_variance(moneyness)
                span = upper.time_to_expiry - lower.time_to_expiry
                weight = 0.0 if span <= 1e-8 else (time_to_expiry - lower.time_to_expiry) / span
                return lower_total * (1.0 - weight) + upper_total * weight
        return self._slices[-1].total_variance(moneyness)


def calibrate_surface(
    snapshots: Sequence[OptionSnapshot],
    *,
    method: str = "wls_regression",
    fallback_vol: float = 0.30,
    min_points: int = 5,
) -> tuple[LiquidityWeightedVolSurface | SVIVolSurface, SurfaceCalibrationResult]:
    """Fit a volatility surface using the requested method."""

    normalized = method.lower()
    if normalized == "auto":
        normalized = "svi" if _surface_observation_count(snapshots) >= min_points else "wls_regression"
    if normalized == "svi":
        return calibrate_svi_surface(snapshots, fallback_vol=fallback_vol, min_points=min_points)
    if normalized not in {"wls_regression", "liquidity_weighted", "liquidity"}:
        raise ValueError(f"Unsupported surface calibration method: {method}")
    return calibrate_wls_surface(snapshots, fallback_vol=fallback_vol)


def calibrate_wls_surface(
    snapshots: Sequence[OptionSnapshot],
    *,
    fallback_vol: float = 0.30,
) -> tuple[LiquidityWeightedVolSurface, SurfaceCalibrationResult]:
    """Fit a weighted regression surface on implied-vol observations."""

    rows: list[list[float]] = []
    vols: list[float] = []
    weights: list[float] = []
    rates: list[float] = []

    for snapshot in snapshots:
        if snapshot.implied_vol is None or snapshot.implied_vol <= 0 or snapshot.time_to_expiry <= 0:
            continue
        if snapshot.contract.underlying_type == UnderlyingType.FUTURE:
            forward = snapshot.underlying_price
        else:
            forward = snapshot.underlying_price * np.exp(
                (snapshot.risk_free_rate - snapshot.dividend_yield) * snapshot.time_to_expiry
            )
        moneyness = log(max(snapshot.contract.strike, 1e-8) / max(forward, 1e-8))
        maturity = snapshot.time_to_expiry
        root_t = sqrt(maturity)
        rows.append(
            [
                1.0,
                moneyness,
                moneyness * moneyness,
                maturity,
                root_t,
                moneyness * root_t,
            ]
        )
        vols.append(snapshot.implied_vol)
        rates.append(snapshot.risk_free_rate)
        liquidity = max(snapshot.quote.volume, 1)
        spread_penalty = max(snapshot.quote.spread_pct, 0.005)
        weights.append(liquidity / spread_penalty)

    if not rows:
        coefficients = np.asarray([fallback_vol, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=float)
        surface = LiquidityWeightedVolSurface(coefficients, 0.14)
        diagnostics = SurfaceCalibrationResult(
            observations=0,
            weighted_r_squared=0.0,
            coefficients=tuple(float(value) for value in coefficients),
            model_name="wls_regression",
        )
        return surface, diagnostics

    design = np.asarray(rows, dtype=float)
    target = np.asarray(vols, dtype=float)
    weight_vector = np.sqrt(np.asarray(weights, dtype=float))
    weighted_design = design * weight_vector[:, None]
    weighted_target = target * weight_vector
    coefficients, _, _, _ = np.linalg.lstsq(weighted_design, weighted_target, rcond=None)
    fitted = design @ coefficients
    centered = target - np.average(target, weights=weights)
    residual_sum = float(np.sum(np.asarray(weights) * (target - fitted) ** 2))
    total_sum = float(np.sum(np.asarray(weights) * centered**2))
    weighted_r_squared = 0.0 if total_sum <= 0 else max(0.0, 1.0 - residual_sum / total_sum)
    surface = LiquidityWeightedVolSurface(coefficients, float(np.mean(rates)))
    diagnostics = SurfaceCalibrationResult(
        observations=len(rows),
        weighted_r_squared=weighted_r_squared,
        coefficients=tuple(float(value) for value in coefficients),
        model_name="wls_regression",
    )
    return surface, diagnostics


def calibrate_svi_surface(
    snapshots: Sequence[OptionSnapshot],
    *,
    fallback_vol: float = 0.30,
    min_points: int = 5,
) -> tuple[SVIVolSurface | LiquidityWeightedVolSurface, SurfaceCalibrationResult]:
    """Fit SVI slices when enough strikes exist, otherwise fall back to WLS."""

    fallback_surface, fallback_diagnostics = calibrate_wls_surface(
        snapshots,
        fallback_vol=fallback_vol,
    )
    groups: dict[object, list[OptionSnapshot]] = {}
    for snapshot in snapshots:
        if snapshot.implied_vol is None or snapshot.implied_vol <= 0 or snapshot.time_to_expiry <= 0:
            continue
        groups.setdefault(snapshot.contract.expiry, []).append(snapshot)

    fitted_slices: list[SVISliceParameters] = []
    residuals: list[float] = []
    weights: list[float] = []
    rates: list[float] = []
    for group in groups.values():
        if len(group) < min_points:
            continue
        result = _fit_svi_slice(group)
        if result is None:
            continue
        params, slice_residuals, slice_weights = result
        fitted_slices.append(params)
        residuals.extend(slice_residuals)
        weights.extend(slice_weights)
        rates.extend(snapshot.risk_free_rate for snapshot in group)

    if not fitted_slices:
        return fallback_surface, fallback_diagnostics

    total_residual = float(np.sum(np.asarray(residuals)))
    total_weight = float(np.sum(np.asarray(weights)))
    weighted_r_squared = 0.0 if total_weight <= 0 else max(0.0, 1.0 - total_residual / total_weight)
    coefficients = tuple(
        value
        for item in fitted_slices
        for value in (item.time_to_expiry, item.a, item.b, item.rho, item.m, item.sigma)
    )
    surface = SVIVolSurface(
        fitted_slices,
        fallback_surface=fallback_surface,
        reference_rate=float(np.mean(rates)) if rates else fallback_surface.reference_rate,
    )
    diagnostics = SurfaceCalibrationResult(
        observations=sum(len(group) for group in groups.values()),
        weighted_r_squared=weighted_r_squared,
        coefficients=coefficients,
        model_name="svi",
    )
    return surface, diagnostics


def _fit_svi_slice(
    snapshots: Sequence[OptionSnapshot],
) -> tuple[SVISliceParameters, list[float], list[float]] | None:
    time_to_expiry = float(snapshots[0].time_to_expiry)
    if time_to_expiry <= 0:
        return None
    rows: list[tuple[float, float, float]] = []
    for snapshot in snapshots:
        if snapshot.implied_vol is None or snapshot.implied_vol <= 0:
            continue
        forward = (
            snapshot.underlying_price
            if snapshot.contract.underlying_type == UnderlyingType.FUTURE
            else snapshot.underlying_price * np.exp(
                (snapshot.risk_free_rate - snapshot.dividend_yield) * snapshot.time_to_expiry
            )
        )
        moneyness = log(max(snapshot.contract.strike, 1e-8) / max(forward, 1e-8))
        total_variance = snapshot.implied_vol**2 * snapshot.time_to_expiry
        weight = max(snapshot.quote.volume, 1) / max(snapshot.quote.spread_pct, 0.005)
        rows.append((moneyness, total_variance, weight))
    if len(rows) < 5:
        return None
    moneyness = np.asarray([row[0] for row in rows], dtype=float)
    target = np.asarray([row[1] for row in rows], dtype=float)
    weight_vector = np.asarray([row[2] for row in rows], dtype=float)
    initial = np.asarray(
        [
            max(float(np.min(target)) * 0.5, 1e-6),
            0.1,
            -0.2,
            float(np.median(moneyness)),
            max(float(np.std(moneyness)), 0.05),
        ],
        dtype=float,
    )
    bounds = [
        (-1.0, 5.0),
        (1e-6, 5.0),
        (-0.999, 0.999),
        (-2.0, 2.0),
        (1e-4, 2.0),
    ]

    def objective(parameters: np.ndarray) -> float:
        a, b, rho, m, sigma = parameters
        modeled = a + b * (rho * (moneyness - m) + np.sqrt((moneyness - m) ** 2 + sigma**2))
        penalty = np.where(modeled <= 1e-8, 1e6, 0.0)
        return float(np.sum(weight_vector * (modeled - target) ** 2 + penalty))

    result = minimize(objective, initial, bounds=bounds, method="L-BFGS-B")
    if not result.success and not np.isfinite(result.fun):
        return None
    a, b, rho, m, sigma = result.x
    params = SVISliceParameters(
        time_to_expiry=time_to_expiry,
        a=float(a),
        b=float(b),
        rho=float(rho),
        m=float(m),
        sigma=float(sigma),
    )
    modeled = np.asarray([params.total_variance(value) for value in moneyness], dtype=float)
    residuals = list(weight_vector * (modeled - target) ** 2)
    centered = target - np.average(target, weights=weight_vector)
    total = list(weight_vector * centered**2)
    return params, residuals, total


def _surface_observation_count(snapshots: Sequence[OptionSnapshot]) -> int:
    return sum(
        1
        for snapshot in snapshots
        if snapshot.implied_vol is not None and snapshot.implied_vol > 0 and snapshot.time_to_expiry > 0
    )
