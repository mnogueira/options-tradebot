"""Shared analytics used by defined-risk short-vol strategies."""

from __future__ import annotations

from math import exp, sqrt
from statistics import NormalDist
from typing import Callable, Sequence

import numpy as np
import pandas as pd

from options_tradebot.config.schema import LiquidityConfig, PricingConfig
from options_tradebot.market.models import GreekVector, OptionSnapshot, UnderlyingType
from options_tradebot.market.pricing import (
    annualized_realized_volatility,
    black_76_price,
    black_scholes_greeks,
    black_scholes_price,
    garch11_forecast_volatility,
)
from options_tradebot.market.surface import calibrate_surface

_N = NormalDist()


def build_surface_and_anchor_vol(
    *,
    chain: Sequence[OptionSnapshot],
    underlying_history: pd.Series,
    pricing: PricingConfig,
) -> tuple[object, float, float, float]:
    returns = np.log(underlying_history.astype(float)).diff().dropna()
    realized_vol = annualized_realized_volatility(returns)
    forecast_vol = garch11_forecast_volatility(returns)
    surface, _ = calibrate_surface(
        list(chain),
        method=pricing.surface_method,
        min_points=pricing.min_surface_points,
        fallback_vol=max(realized_vol, forecast_vol, pricing.minimum_volatility),
    )
    anchor_vol = max(
        pricing.surface_weight * max(realized_vol, pricing.minimum_volatility)
        + pricing.forecast_weight * max(forecast_vol, pricing.minimum_volatility),
        pricing.minimum_volatility,
    )
    return surface, anchor_vol, realized_vol, forecast_vol


def fair_value_for_snapshot(snapshot: OptionSnapshot, *, volatility: float) -> float:
    if snapshot.contract.underlying_type == UnderlyingType.FUTURE:
        return black_76_price(
            forward=snapshot.forward_price,
            strike=snapshot.contract.strike,
            time_to_expiry=snapshot.time_to_expiry,
            rate=snapshot.risk_free_rate,
            volatility=volatility,
            option_type=snapshot.contract.option_type,
        )
    return black_scholes_price(
        spot=snapshot.underlying_price,
        strike=snapshot.contract.strike,
        time_to_expiry=snapshot.time_to_expiry,
        rate=snapshot.risk_free_rate,
        dividend_yield=snapshot.dividend_yield,
        volatility=volatility,
        option_type=snapshot.contract.option_type,
    )


def snapshot_greeks(snapshot: OptionSnapshot, *, fallback_volatility: float) -> GreekVector:
    if snapshot.broker_greeks is not None:
        return snapshot.broker_greeks
    volatility = snapshot.implied_vol if snapshot.implied_vol is not None and snapshot.implied_vol > 0 else fallback_volatility
    if snapshot.contract.underlying_type == UnderlyingType.FUTURE:
        spot = snapshot.forward_price
        dividend_yield = 0.0
    else:
        spot = snapshot.underlying_price
        dividend_yield = snapshot.dividend_yield
    return black_scholes_greeks(
        spot=spot,
        strike=snapshot.contract.strike,
        time_to_expiry=snapshot.time_to_expiry,
        rate=snapshot.risk_free_rate,
        dividend_yield=dividend_yield,
        volatility=max(volatility, 0.01),
        option_type=snapshot.contract.option_type,
    )


def is_short_leg_tradeable(snapshot: OptionSnapshot, liquidity: LiquidityConfig, *, min_dte: int, max_dte: int) -> bool:
    if snapshot.dte < min_dte or snapshot.dte > max_dte:
        return False
    if snapshot.quote.volume < liquidity.min_short_leg_volume:
        return False
    if snapshot.quote.spread_pct > liquidity.max_short_leg_spread_pct:
        return False
    if snapshot.quote.open_interest is not None and snapshot.quote.open_interest < liquidity.min_open_interest:
        return False
    return snapshot.bid_price > 0 and snapshot.ask_price > 0


def is_long_leg_tradeable(snapshot: OptionSnapshot, liquidity: LiquidityConfig, *, min_dte: int, max_dte: int, max_spread_pct: float | None = None) -> bool:
    allowed_spread = liquidity.max_long_leg_spread_pct if max_spread_pct is None else max_spread_pct
    if snapshot.dte < min_dte or snapshot.dte > max_dte:
        return False
    if snapshot.quote.volume < liquidity.min_long_leg_volume:
        return False
    if snapshot.quote.spread_pct > allowed_spread:
        return False
    if snapshot.quote.open_interest is not None and snapshot.quote.open_interest < max(liquidity.min_open_interest // 4, 1):
        return False
    return snapshot.bid_price > 0 and snapshot.ask_price > 0


def liquidity_score(*snapshots: OptionSnapshot) -> float:
    total_volume = sum(snapshot.quote.volume + (snapshot.quote.open_interest or 0) for snapshot in snapshots)
    spread_penalty = 1.0 + sum(snapshot.quote.spread_pct for snapshot in snapshots)
    return float(np.log1p(max(total_volume, 0.0)) / max(spread_penalty, 1.0))


def spread_close_debit(snapshots_by_symbol: dict[str, OptionSnapshot], legs: Sequence[tuple[str, str]]) -> float | None:
    debit = 0.0
    for symbol, entry_action in legs:
        snapshot = snapshots_by_symbol.get(symbol)
        if snapshot is None:
            return None
        if entry_action.upper() == "SELL":
            debit += snapshot.ask_price
        else:
            debit -= snapshot.bid_price
    return max(debit, 0.0)


def distribution_metrics(
    *,
    spot: float,
    time_to_expiry: float,
    volatility: float,
    drift: float,
    grid_size: int,
    payoff: Callable[[np.ndarray], np.ndarray],
) -> tuple[float, float, float, float]:
    if time_to_expiry <= 0 or volatility <= 0:
        terminal = np.asarray([spot], dtype=float)
    else:
        quantiles = np.linspace(0.005, 0.995, max(grid_size, 33))
        shocks = np.asarray([_N.inv_cdf(float(value)) for value in quantiles], dtype=float)
        terminal = spot * np.exp((drift - 0.5 * volatility * volatility) * time_to_expiry + volatility * sqrt(time_to_expiry) * shocks)
    pnl = payoff(terminal)
    probability_of_profit = float(np.mean(pnl > 0))
    cvar_95 = float(_cvar_95(-pnl))
    probability_of_touch = float(min(max(2.0 * (1.0 - probability_of_profit), 0.0), 1.0))
    expected_value = float(np.mean(pnl))
    return expected_value, probability_of_profit, probability_of_touch, cvar_95


def _cvar_95(losses: np.ndarray) -> float:
    if losses.size == 0:
        return 0.0
    quantile = float(np.quantile(losses, 0.95))
    tail = losses[losses >= quantile]
    if tail.size == 0:
        return quantile
    return float(np.mean(tail))
