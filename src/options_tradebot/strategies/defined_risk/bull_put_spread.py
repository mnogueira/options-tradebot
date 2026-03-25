"""Bull put spread candidate generation."""

from __future__ import annotations

from options_tradebot.config.schema import LiquidityConfig, PricingConfig, VerticalStrategyConfig
from options_tradebot.market.models import OptionKind, OptionSnapshot
from options_tradebot.strategies.defined_risk.analytics import (
    distribution_metrics,
    fair_value_for_snapshot,
    is_long_leg_tradeable,
    is_short_leg_tradeable,
    liquidity_score,
    snapshot_greeks,
)
from options_tradebot.strategies.defined_risk.types import CandidateMetrics, StrategyLeg, StrategyTradeCandidate


def build_bull_put_candidates(
    *,
    venue: str,
    chain: list[OptionSnapshot],
    anchor_vol: float,
    vol_regime_score: float,
    liquidity: LiquidityConfig,
    pricing: PricingConfig,
    strategy: VerticalStrategyConfig,
    min_dte: int,
    max_dte: int,
) -> list[StrategyTradeCandidate]:
    puts = [
        snapshot
        for snapshot in chain
        if snapshot.contract.option_type == OptionKind.PUT
        and snapshot.bid_price > 0
        and snapshot.ask_price > 0
    ]
    candidates: list[StrategyTradeCandidate] = []
    for short_snapshot in puts:
        if not is_short_leg_tradeable(short_snapshot, liquidity, min_dte=min_dte, max_dte=max_dte):
            continue
        short_greeks = snapshot_greeks(short_snapshot, fallback_volatility=anchor_vol)
        if not (strategy.short_delta_min <= abs(short_greeks.delta) <= strategy.short_delta_max):
            continue
        if short_snapshot.contract.strike >= short_snapshot.underlying_price:
            continue
        for long_snapshot in puts:
            if long_snapshot.contract.expiry != short_snapshot.contract.expiry:
                continue
            if long_snapshot.contract.strike >= short_snapshot.contract.strike:
                continue
            if not is_long_leg_tradeable(long_snapshot, liquidity, min_dte=min_dte, max_dte=max_dte):
                continue
            width = short_snapshot.contract.strike - long_snapshot.contract.strike
            max_width = max(short_snapshot.underlying_price * strategy.width_pct_of_spot_cap, 0.10)
            if width <= 0 or width > max_width:
                continue
            entry_credit = short_snapshot.bid_price - long_snapshot.ask_price
            if entry_credit < strategy.min_credit or entry_credit >= width:
                continue
            short_fair = fair_value_for_snapshot(short_snapshot, volatility=anchor_vol)
            long_fair = fair_value_for_snapshot(long_snapshot, volatility=anchor_vol)
            fair_credit = max(short_fair - long_fair, 0.0)
            multiplier = short_snapshot.contract.contract_multiplier
            spread_cost = 0.5 * (short_snapshot.quote.spread + long_snapshot.quote.spread) * multiplier
            max_loss = (width - entry_credit) * multiplier
            if max_loss <= 0:
                continue

            def payoff(terminal_prices):
                pnl = entry_credit - (short_snapshot.contract.strike - terminal_prices).clip(min=0.0)
                pnl += (long_snapshot.contract.strike - terminal_prices).clip(min=0.0)
                return pnl * multiplier - spread_cost

            expected_value, probability_of_profit, probability_of_touch, cvar_95 = distribution_metrics(
                spot=short_snapshot.underlying_price,
                time_to_expiry=short_snapshot.time_to_expiry,
                volatility=anchor_vol,
                drift=pricing.physical_drift,
                grid_size=pricing.distribution_grid_size,
                payoff=payoff,
            )
            expected_value_after_costs = expected_value
            if expected_value_after_costs < strategy.min_expected_value:
                continue
            long_greeks = snapshot_greeks(long_snapshot, fallback_volatility=anchor_vol)
            net_delta = (long_greeks.delta - short_greeks.delta) * multiplier
            net_gamma = (long_greeks.gamma - short_greeks.gamma) * multiplier
            net_vega = (long_greeks.vega - short_greeks.vega) * multiplier
            net_theta = (long_greeks.theta - short_greeks.theta) * multiplier
            candidates.append(
                StrategyTradeCandidate(
                    candidate_id=(
                        f"bull_put:{venue}:{short_snapshot.contract.underlying}:"
                        f"{short_snapshot.contract.expiry.isoformat()}:{short_snapshot.contract.symbol}:{long_snapshot.contract.symbol}"
                    ),
                    strategy_name="bull_put_spread",
                    venue=venue,
                    market=short_snapshot.market,
                    underlying=short_snapshot.contract.underlying,
                    expiry=short_snapshot.contract.expiry.isoformat(),
                    legs=(
                        StrategyLeg(
                            symbol=short_snapshot.contract.symbol,
                            action="SELL",
                            option_type="put",
                            strike=short_snapshot.contract.strike,
                            expiry=short_snapshot.contract.expiry.isoformat(),
                            contract_multiplier=multiplier,
                        ),
                        StrategyLeg(
                            symbol=long_snapshot.contract.symbol,
                            action="BUY",
                            option_type="put",
                            strike=long_snapshot.contract.strike,
                            expiry=long_snapshot.contract.expiry.isoformat(),
                            contract_multiplier=multiplier,
                        ),
                    ),
                    entry_credit=entry_credit,
                    fair_value=fair_credit,
                    max_loss_per_contract=max_loss,
                    target_debit=max(entry_credit * (1.0 - strategy.target_capture_pct), 0.01),
                    stop_debit=min(width, max(entry_credit * strategy.stop_multiple, 0.02)),
                    breakeven_low=short_snapshot.contract.strike - entry_credit,
                    breakeven_high=None,
                    net_delta_per_contract=net_delta,
                    net_gamma_per_contract=net_gamma,
                    net_vega_per_contract=net_vega,
                    net_theta_per_contract=net_theta,
                    metrics=CandidateMetrics(
                        expected_value=expected_value,
                        expected_value_after_costs=expected_value_after_costs,
                        probability_of_profit=probability_of_profit,
                        probability_of_touch=probability_of_touch,
                        cvar_95=cvar_95,
                        return_on_risk=expected_value_after_costs / max_loss,
                        liquidity_score=liquidity_score(short_snapshot, long_snapshot),
                        vol_regime_score=vol_regime_score,
                    ),
                    score=0.0,
                    thesis="short downside skew with capped tail risk",
                )
            )
    return candidates
