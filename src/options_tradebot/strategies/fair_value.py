"""Market-making-inspired fair-value trading strategy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Sequence

import numpy as np
import pandas as pd

from options_tradebot.config.settings import AppSettings, default_settings
from options_tradebot.market.models import GreekVector, OptionKind, OptionSnapshot, UnderlyingType
from options_tradebot.market.pricing import (
    annualized_realized_volatility,
    black_76_price,
    black_scholes_greeks,
    black_scholes_price,
    garch11_forecast_volatility,
)
from options_tradebot.market.surface import LiquidityWeightedVolSurface, calibrate_surface
from options_tradebot.risk.sizing import GreekLimits, size_option_position


class DirectionBias(StrEnum):
    """Directional regime inferred from the underlying."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    """Strategy diagnostics for a candidate option."""

    snapshot: OptionSnapshot
    fair_volatility: float
    fair_value: float
    greeks: GreekVector
    edge_reais: float
    edge_pct: float
    edge_to_spread: float
    score: float


@dataclass(frozen=True, slots=True)
class StrategySignal:
    """Tradeable strategy output."""

    action: str
    contract_symbol: str | None
    underlying: str | None
    contracts: int
    entry_price: float | None
    target_price: float | None
    stop_price: float | None
    fair_value: float | None
    fair_volatility: float | None
    reason: str
    greeks: GreekVector | None = None
    score: float = 0.0


class FairValueOptionsStrategy:
    """Selective long-premium strategy adapted from a market-making framework."""

    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or default_settings()

    def market_bias(self, underlying_history: pd.Series) -> DirectionBias:
        """Infer a simple trend regime from the underlying history."""

        closes = underlying_history.dropna().astype(float)
        if closes.size < self.settings.strategy.slow_ema:
            if closes.size >= 3:
                if closes.iloc[-1] > closes.iloc[0]:
                    return DirectionBias.BULLISH
                if closes.iloc[-1] < closes.iloc[0]:
                    return DirectionBias.BEARISH
            return DirectionBias.NEUTRAL
        fast = closes.ewm(span=self.settings.strategy.fast_ema, adjust=False).mean().iloc[-1]
        slow = closes.ewm(span=self.settings.strategy.slow_ema, adjust=False).mean().iloc[-1]
        latest = closes.iloc[-1]
        if latest > fast > slow:
            return DirectionBias.BULLISH
        if latest < fast < slow:
            return DirectionBias.BEARISH
        return DirectionBias.NEUTRAL

    def select_signal(
        self,
        *,
        chain: Sequence[OptionSnapshot],
        underlying_history: pd.Series,
        account_equity: float,
        current_portfolio_greeks: GreekVector | None = None,
        surface: LiquidityWeightedVolSurface | None = None,
    ) -> StrategySignal:
        """Return the best long-premium signal for one underlying at one timestamp."""

        if not chain:
            return StrategySignal(
                action="HOLD",
                contract_symbol=None,
                underlying=None,
                contracts=0,
                entry_price=None,
                target_price=None,
                stop_price=None,
                fair_value=None,
                fair_volatility=None,
                greeks=None,
                reason="empty_chain",
            )
        bias = self.market_bias(underlying_history)
        if bias == DirectionBias.NEUTRAL:
            return StrategySignal(
                action="HOLD",
                contract_symbol=None,
                underlying=chain[0].contract.underlying,
                contracts=0,
                entry_price=None,
                target_price=None,
                stop_price=None,
                fair_value=None,
                fair_volatility=None,
                greeks=None,
                reason="neutral_regime",
            )
        fitted_surface = surface
        if fitted_surface is None:
            fitted_surface, _ = calibrate_surface(
                chain,
                method=self.settings.strategy.surface_method,
                min_points=self.settings.scanner.min_surface_points,
            )
        returns = np.log(underlying_history.astype(float)).diff().dropna().tail(
            self.settings.strategy.realized_vol_lookback
        )
        realized_vol = annualized_realized_volatility(returns)
        forecast_vol = garch11_forecast_volatility(
            returns,
            omega=self.settings.strategy.garch_omega,
            alpha=self.settings.strategy.garch_alpha,
            beta=self.settings.strategy.garch_beta,
        )
        evaluations = [
            self.evaluate_snapshot(
                snapshot=snapshot,
                bias=bias,
                realized_vol=realized_vol,
                forecast_vol=forecast_vol,
                surface=fitted_surface,
            )
            for snapshot in chain
        ]
        viable = [evaluation for evaluation in evaluations if evaluation is not None]
        if not viable:
            return StrategySignal(
                action="HOLD",
                contract_symbol=None,
                underlying=chain[0].contract.underlying,
                contracts=0,
                entry_price=None,
                target_price=None,
                stop_price=None,
                fair_value=None,
                fair_volatility=None,
                greeks=None,
                reason="no_viable_candidate",
            )
        best = max(viable, key=lambda item: item.score)
        sizing = size_option_position(
            account_equity=account_equity,
            premium=best.snapshot.ask_price,
            contract_multiplier=best.snapshot.contract.contract_multiplier,
            risk_per_trade_pct=self.settings.risk.risk_per_trade_pct,
            max_contracts=self.settings.risk.max_contracts,
            greek_limits=GreekLimits(
                max_abs_delta=self.settings.risk.max_abs_delta,
                max_abs_gamma=self.settings.risk.max_abs_gamma,
                max_abs_vega=self.settings.risk.max_abs_vega,
            ),
            current_portfolio_greeks=current_portfolio_greeks,
            candidate_greeks=GreekVector(
                delta=best.greeks.delta * best.snapshot.contract.contract_multiplier,
                gamma=best.greeks.gamma * best.snapshot.contract.contract_multiplier,
                vega=best.greeks.vega * best.snapshot.contract.contract_multiplier,
                theta=best.greeks.theta * best.snapshot.contract.contract_multiplier,
            ),
        )
        if sizing.rejected or best.score <= 0:
            return StrategySignal(
                action="HOLD",
                contract_symbol=None,
                underlying=best.snapshot.contract.underlying,
                contracts=0,
                entry_price=None,
                target_price=None,
                stop_price=None,
                fair_value=best.fair_value,
                fair_volatility=best.fair_volatility,
                greeks=best.greeks,
                reason=sizing.reason if sizing.rejected else "non_positive_score",
                score=max(best.score, 0.0),
            )
        action = "BUY_CALL" if best.snapshot.contract.option_type == OptionKind.CALL else "BUY_PUT"
        entry_price = best.snapshot.ask_price
        target_price = max(
            entry_price * (1.0 + self.settings.strategy.take_profit_pct),
            min(best.fair_value, entry_price * 2.0),
        )
        stop_price = max(
            entry_price * (1.0 - self.settings.strategy.stop_loss_pct),
            0.01,
        )
        return StrategySignal(
            action=action,
            contract_symbol=best.snapshot.contract.symbol,
            underlying=best.snapshot.contract.underlying,
            contracts=sizing.contracts,
            entry_price=entry_price,
            target_price=target_price,
            stop_price=stop_price,
            fair_value=best.fair_value,
            fair_volatility=best.fair_volatility,
            greeks=best.greeks,
            reason="approved",
            score=best.score,
        )

    def evaluate_snapshot(
        self,
        *,
        snapshot: OptionSnapshot,
        bias: DirectionBias,
        realized_vol: float,
        forecast_vol: float,
        surface: LiquidityWeightedVolSurface,
    ) -> CandidateEvaluation | None:
        """Score one candidate option."""

        universe = self.settings.universe
        if snapshot.dte < universe.min_dte or snapshot.dte > universe.max_dte:
            return None
        if snapshot.quote.volume < universe.min_daily_volume:
            return None
        if (snapshot.quote.open_interest or 0) < universe.min_open_interest:
            return None
        if snapshot.quote.spread_pct > universe.max_spread_pct:
            return None
        premium_cash = snapshot.ask_price * snapshot.contract.contract_multiplier
        if not (
            universe.min_premium_per_contract
            <= premium_cash
            <= universe.max_premium_per_contract
        ):
            return None
        if bias == DirectionBias.BULLISH and snapshot.contract.option_type != OptionKind.CALL:
            return None
        if bias == DirectionBias.BEARISH and snapshot.contract.option_type != OptionKind.PUT:
            return None
        surface_vol = surface.volatility_for_snapshot(snapshot)
        forecast_component = max(realized_vol, forecast_vol, 0.05)
        fair_vol = max(
            self.settings.strategy.fair_vol_surface_weight * surface_vol
            + self.settings.strategy.fair_vol_forecast_weight * forecast_component,
            0.05,
        )
        if snapshot.contract.underlying_type == UnderlyingType.FUTURE:
            fair_value = black_76_price(
                forward=snapshot.forward_price,
                strike=snapshot.contract.strike,
                time_to_expiry=snapshot.time_to_expiry,
                rate=snapshot.risk_free_rate,
                volatility=fair_vol,
                option_type=snapshot.contract.option_type,
            )
            spot_for_greeks = snapshot.forward_price
        else:
            fair_value = black_scholes_price(
                spot=snapshot.underlying_price,
                strike=snapshot.contract.strike,
                time_to_expiry=snapshot.time_to_expiry,
                rate=snapshot.risk_free_rate,
                dividend_yield=snapshot.dividend_yield,
                volatility=fair_vol,
                option_type=snapshot.contract.option_type,
            )
            spot_for_greeks = snapshot.underlying_price
        model_greeks = black_scholes_greeks(
            spot=spot_for_greeks,
            strike=snapshot.contract.strike,
            time_to_expiry=snapshot.time_to_expiry,
            rate=snapshot.risk_free_rate,
            dividend_yield=snapshot.dividend_yield,
            volatility=fair_vol,
            option_type=snapshot.contract.option_type,
        )
        greeks = snapshot.broker_greeks or model_greeks
        abs_delta = abs(greeks.delta)
        if abs_delta < universe.min_delta or abs_delta > universe.max_delta:
            return None
        edge_reais = fair_value - snapshot.ask_price
        edge_pct = edge_reais / max(snapshot.ask_price, 1e-8)
        edge_to_spread = edge_reais / max(snapshot.quote.spread, 0.01)
        if not isfinite(edge_pct):
            return None
        if edge_pct < self.settings.strategy.edge_threshold_pct:
            return None
        if edge_to_spread < self.settings.strategy.min_edge_to_spread:
            return None
        liquidity_score = float(np.log1p(snapshot.quote.volume)) / (1.0 + snapshot.quote.spread_pct)
        score = edge_pct * edge_to_spread * liquidity_score
        return CandidateEvaluation(
            snapshot=snapshot,
            fair_volatility=fair_vol,
            fair_value=fair_value,
            greeks=greeks,
            edge_reais=edge_reais,
            edge_pct=edge_pct,
            edge_to_spread=edge_to_spread,
            score=score,
        )
