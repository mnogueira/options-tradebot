"""Liquidity-aware volatility surface calibration."""

from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt
from typing import Sequence

import numpy as np

from options_tradebot.market.models import OptionSnapshot, UnderlyingType


@dataclass(frozen=True, slots=True)
class SurfaceCalibrationResult:
    """Diagnostics from the fitted surface."""

    observations: int
    weighted_r_squared: float
    coefficients: tuple[float, ...]


class LiquidityWeightedVolSurface:
    """Smooth but simple surface fitted on log-moneyness and maturity."""

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
        features = np.asarray(
            [
                1.0,
                moneyness,
                moneyness * moneyness,
                time_to_expiry,
                sqrt(time_to_expiry),
                moneyness * sqrt(time_to_expiry),
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


def calibrate_surface(
    snapshots: Sequence[OptionSnapshot],
    *,
    fallback_vol: float = 0.30,
) -> tuple[LiquidityWeightedVolSurface, SurfaceCalibrationResult]:
    """Fit a weighted regression surface on implied vol observations."""

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
        rows.append(
            [
                1.0,
                moneyness,
                moneyness * moneyness,
                maturity,
                sqrt(maturity),
                moneyness * sqrt(maturity),
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
    )
    return surface, diagnostics
