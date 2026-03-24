"""Pricing models used by the strategy."""

from __future__ import annotations

from math import exp, log, sqrt
from statistics import NormalDist
from typing import Iterable

import numpy as np
from scipy.optimize import brentq

from options_tradebot.market.models import GreekVector, OptionKind, TRADING_DAYS_PER_YEAR

_N = NormalDist()


def _pdf(value: float) -> float:
    return exp(-0.5 * value * value) / sqrt(2.0 * np.pi)


def _d1_d2(
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    dividend_yield: float,
    volatility: float,
) -> tuple[float, float]:
    variance = volatility * volatility
    root_t = sqrt(time_to_expiry)
    d1 = (
        log(spot / strike)
        + (rate - dividend_yield + 0.5 * variance) * time_to_expiry
    ) / (volatility * root_t)
    d2 = d1 - volatility * root_t
    return d1, d2


def _intrinsic_value(spot: float, strike: float, option_type: OptionKind) -> float:
    if option_type == OptionKind.CALL:
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


def black_scholes_price(
    *,
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    dividend_yield: float,
    volatility: float,
    option_type: OptionKind,
) -> float:
    """Price a European option on spot under Black-Scholes."""

    if time_to_expiry <= 0 or volatility <= 0:
        return _intrinsic_value(spot, strike, option_type)
    d1, d2 = _d1_d2(spot, strike, time_to_expiry, rate, dividend_yield, volatility)
    discounted_spot = spot * exp(-dividend_yield * time_to_expiry)
    discounted_strike = strike * exp(-rate * time_to_expiry)
    if option_type == OptionKind.CALL:
        return discounted_spot * _N.cdf(d1) - discounted_strike * _N.cdf(d2)
    return discounted_strike * _N.cdf(-d2) - discounted_spot * _N.cdf(-d1)


def black_76_price(
    *,
    forward: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    volatility: float,
    option_type: OptionKind,
) -> float:
    """Price an option on futures under Black-76."""

    if time_to_expiry <= 0 or volatility <= 0:
        return _intrinsic_value(forward, strike, option_type)
    root_t = sqrt(time_to_expiry)
    d1 = (log(forward / strike) + 0.5 * volatility * volatility * time_to_expiry) / (
        volatility * root_t
    )
    d2 = d1 - volatility * root_t
    discount = exp(-rate * time_to_expiry)
    if option_type == OptionKind.CALL:
        return discount * (forward * _N.cdf(d1) - strike * _N.cdf(d2))
    return discount * (strike * _N.cdf(-d2) - forward * _N.cdf(-d1))


def black_scholes_greeks(
    *,
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    dividend_yield: float,
    volatility: float,
    option_type: OptionKind,
) -> GreekVector:
    """Return Black-Scholes Greeks for one contract.

    Theta is annualized and negative for long options under the usual decay convention.
    """

    if time_to_expiry <= 0 or volatility <= 0:
        intrinsic_delta = 1.0 if (option_type == OptionKind.CALL and spot > strike) else 0.0
        if option_type == OptionKind.PUT and spot < strike:
            intrinsic_delta = -1.0
        return GreekVector(delta=intrinsic_delta, gamma=0.0, vega=0.0, theta=0.0)
    d1, d2 = _d1_d2(spot, strike, time_to_expiry, rate, dividend_yield, volatility)
    root_t = sqrt(time_to_expiry)
    discount_q = exp(-dividend_yield * time_to_expiry)
    discount_r = exp(-rate * time_to_expiry)
    pdf = _pdf(d1)
    if option_type == OptionKind.CALL:
        delta = discount_q * _N.cdf(d1)
        theta = (
            -(spot * discount_q * pdf * volatility) / (2.0 * root_t)
            - rate * strike * discount_r * _N.cdf(d2)
            + dividend_yield * spot * discount_q * _N.cdf(d1)
        )
    else:
        delta = discount_q * (_N.cdf(d1) - 1.0)
        theta = (
            -(spot * discount_q * pdf * volatility) / (2.0 * root_t)
            + rate * strike * discount_r * _N.cdf(-d2)
            - dividend_yield * spot * discount_q * _N.cdf(-d1)
        )
    gamma = discount_q * pdf / (spot * volatility * root_t)
    vega = spot * discount_q * pdf * root_t
    return GreekVector(delta=delta, gamma=gamma, vega=vega, theta=theta)


def implied_volatility(
    *,
    market_price: float,
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    dividend_yield: float,
    option_type: OptionKind,
    lower: float = 1e-4,
    upper: float = 5.0,
) -> float | None:
    """Invert the Black-Scholes price into an implied volatility."""

    if market_price <= 0 or time_to_expiry <= 0:
        return None

    def objective(vol: float) -> float:
        return black_scholes_price(
            spot=spot,
            strike=strike,
            time_to_expiry=time_to_expiry,
            rate=rate,
            dividend_yield=dividend_yield,
            volatility=vol,
            option_type=option_type,
        ) - market_price

    try:
        return float(brentq(objective, lower, upper))
    except ValueError:
        return None


def corrado_su_price(
    *,
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    dividend_yield: float,
    volatility: float,
    skewness: float,
    kurtosis: float,
    option_type: OptionKind,
) -> float:
    """Corrado-Su option pricing adjustment used in the B3 manual for illiquid names."""

    if time_to_expiry <= 0 or volatility <= 0:
        return _intrinsic_value(spot, strike, option_type)

    # The Corrado-Su expansion is written for calls, so we price the call-adjusted leg
    # and then use put-call parity to recover the put value when requested.
    call_bs_value = black_scholes_price(
        spot=spot,
        strike=strike,
        time_to_expiry=time_to_expiry,
        rate=rate,
        dividend_yield=dividend_yield,
        volatility=volatility,
        option_type=OptionKind.CALL,
    )
    root_t = sqrt(time_to_expiry)
    w = (
        skewness / 6.0 * volatility**3 * time_to_expiry**1.5
        + kurtosis / 24.0 * volatility**4 * time_to_expiry**2.0
    )
    d = (
        log(spot / strike)
        + (rate - dividend_yield + 0.5 * volatility * volatility) * time_to_expiry
        - log(1.0 + w)
    ) / (volatility * root_t)
    q3 = (
        spot
        * volatility
        * root_t
        * (2.0 * volatility * root_t - d)
        * _pdf(d)
        / (6.0 * (1.0 + w))
    )
    q4 = (
        spot
        * volatility
        * root_t
        * (d * d - 3.0 * d * volatility * root_t + 3.0 * volatility**2 * time_to_expiry - 1.0)
        * _pdf(d)
        / (24.0 * (1.0 + w))
    )
    call_value = max(call_bs_value + skewness * q3 + (kurtosis - 3.0) * q4, 0.0)
    if option_type == OptionKind.CALL:
        return call_value
    parity_leg = spot * exp(-dividend_yield * time_to_expiry) - strike * exp(
        -rate * time_to_expiry
    )
    return max(call_value - parity_leg, 0.0)


def annualized_realized_volatility(
    returns: Iterable[float],
    annualization: int = int(TRADING_DAYS_PER_YEAR),
) -> float:
    """Compute annualized realized volatility from log returns."""

    values = np.asarray(list(returns), dtype=float)
    if values.size < 2:
        return 0.0
    return float(np.std(values, ddof=1) * sqrt(annualization))


def garch11_forecast_volatility(
    returns: Iterable[float],
    *,
    omega: float = 1e-6,
    alpha: float = 0.08,
    beta: float = 0.90,
    annualization: int = int(TRADING_DAYS_PER_YEAR),
) -> float:
    """A lightweight GARCH(1,1) volatility forecast."""

    values = np.asarray(list(returns), dtype=float)
    if values.size < 2:
        return 0.0
    unconditional = max(np.var(values, ddof=1), 1e-8)
    variance = unconditional
    for value in values:
        variance = omega + alpha * value * value + beta * variance
    return float(sqrt(max(variance, 0.0)) * sqrt(annualization))
