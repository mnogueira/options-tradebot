"""Iron condor candidate generation."""

from __future__ import annotations

from options_tradebot.config.schema import IronCondorStrategyConfig, LiquidityConfig, PricingConfig
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


def build_iron_condor_candidates(
    *,
    venue: str,
    chain: list[OptionSnapshot],
    anchor_vol: float,
    vol_regime_score: float,
    liquidity: LiquidityConfig,
    pricing: PricingConfig,
    strategy: IronCondorStrategyConfig,
    min_dte: int,
    max_dte: int,
) -> list[StrategyTradeCandidate]:
    puts = [
        snapshot
        for snapshot in chain
        if snapshot.contract.option_type == OptionKind.PUT and snapshot.bid_price > 0 and snapshot.ask_price > 0
    ]
    calls = [
        snapshot
        for snapshot in chain
        if snapshot.contract.option_type == OptionKind.CALL and snapshot.bid_price > 0 and snapshot.ask_price > 0
    ]
    candidates: list[StrategyTradeCandidate] = []
    for short_put in puts:
        if not is_short_leg_tradeable(short_put, liquidity, min_dte=min_dte, max_dte=max_dte):
            continue
        short_put_greeks = snapshot_greeks(short_put, fallback_volatility=anchor_vol)
        if not (strategy.short_delta_min <= abs(short_put_greeks.delta) <= strategy.short_delta_max):
            continue
        for long_put in puts:
            if long_put.contract.expiry != short_put.contract.expiry:
                continue
            if long_put.contract.strike >= short_put.contract.strike:
                continue
            if not is_long_leg_tradeable(long_put, liquidity, min_dte=min_dte, max_dte=max_dte, max_spread_pct=liquidity.max_condor_leg_spread_pct):
                continue
            put_width = short_put.contract.strike - long_put.contract.strike
            if put_width <= 0:
                continue
            put_credit = short_put.bid_price - long_put.ask_price
            if put_credit <= 0:
                continue
            for short_call in calls:
                if short_call.contract.expiry != short_put.contract.expiry:
                    continue
                if not is_short_leg_tradeable(short_call, liquidity, min_dte=min_dte, max_dte=max_dte):
                    continue
                short_call_greeks = snapshot_greeks(short_call, fallback_volatility=anchor_vol)
                if not (strategy.short_delta_min <= abs(short_call_greeks.delta) <= strategy.short_delta_max):
                    continue
                if short_call.contract.strike <= short_put.contract.strike:
                    continue
                for long_call in calls:
                    if long_call.contract.expiry != short_call.contract.expiry:
                        continue
                    if long_call.contract.strike <= short_call.contract.strike:
                        continue
                    if not is_long_leg_tradeable(long_call, liquidity, min_dte=min_dte, max_dte=max_dte, max_spread_pct=liquidity.max_condor_leg_spread_pct):
                        continue
                    call_width = long_call.contract.strike - short_call.contract.strike
                    if call_width <= 0:
                        continue
                    total_width = put_width + call_width
                    if total_width > max(short_put.underlying_price * strategy.max_total_width_pct_of_spot, 0.20):
                        continue
                    call_credit = short_call.bid_price - long_call.ask_price
                    if call_credit <= 0:
                        continue
                    total_credit = put_credit + call_credit
                    if total_credit < strategy.min_total_credit:
                        continue
                    multiplier = short_put.contract.contract_multiplier
                    spread_cost = 0.5 * (
                        short_put.quote.spread
                        + long_put.quote.spread
                        + short_call.quote.spread
                        + long_call.quote.spread
                    ) * multiplier
                    max_loss = (max(put_width, call_width) - total_credit) * multiplier
                    if max_loss <= 0:
                        continue
                    fair_credit = max(
                        fair_value_for_snapshot(short_put, volatility=anchor_vol)
                        - fair_value_for_snapshot(long_put, volatility=anchor_vol)
                        + fair_value_for_snapshot(short_call, volatility=anchor_vol)
                        - fair_value_for_snapshot(long_call, volatility=anchor_vol),
                        0.0,
                    )

                    def payoff(terminal_prices):
                        pnl = total_credit
                        pnl -= (short_put.contract.strike - terminal_prices).clip(min=0.0)
                        pnl += (long_put.contract.strike - terminal_prices).clip(min=0.0)
                        pnl -= (terminal_prices - short_call.contract.strike).clip(min=0.0)
                        pnl += (terminal_prices - long_call.contract.strike).clip(min=0.0)
                        return pnl * multiplier - spread_cost

                    expected_value, probability_of_profit, probability_of_touch, cvar_95 = distribution_metrics(
                        spot=short_put.underlying_price,
                        time_to_expiry=short_put.time_to_expiry,
                        volatility=anchor_vol,
                        drift=pricing.physical_drift,
                        grid_size=pricing.distribution_grid_size,
                        payoff=payoff,
                    )
                    if expected_value < strategy.min_expected_value:
                        continue
                    long_put_greeks = snapshot_greeks(long_put, fallback_volatility=anchor_vol)
                    long_call_greeks = snapshot_greeks(long_call, fallback_volatility=anchor_vol)
                    net_delta = (long_put_greeks.delta + long_call_greeks.delta - short_put_greeks.delta - short_call_greeks.delta) * multiplier
                    net_gamma = (long_put_greeks.gamma + long_call_greeks.gamma - short_put_greeks.gamma - short_call_greeks.gamma) * multiplier
                    net_vega = (long_put_greeks.vega + long_call_greeks.vega - short_put_greeks.vega - short_call_greeks.vega) * multiplier
                    net_theta = (long_put_greeks.theta + long_call_greeks.theta - short_put_greeks.theta - short_call_greeks.theta) * multiplier
                    candidates.append(
                        StrategyTradeCandidate(
                            candidate_id=(
                                f"iron_condor:{venue}:{short_put.contract.underlying}:{short_put.contract.expiry.isoformat()}:"
                                f"{short_put.contract.symbol}:{long_put.contract.symbol}:{short_call.contract.symbol}:{long_call.contract.symbol}"
                            ),
                            strategy_name="iron_condor",
                            venue=venue,
                            market=short_put.market,
                            underlying=short_put.contract.underlying,
                            expiry=short_put.contract.expiry.isoformat(),
                            legs=(
                                StrategyLeg(short_put.contract.symbol, "SELL", "put", short_put.contract.strike, short_put.contract.expiry.isoformat(), multiplier),
                                StrategyLeg(long_put.contract.symbol, "BUY", "put", long_put.contract.strike, long_put.contract.expiry.isoformat(), multiplier),
                                StrategyLeg(short_call.contract.symbol, "SELL", "call", short_call.contract.strike, short_call.contract.expiry.isoformat(), multiplier),
                                StrategyLeg(long_call.contract.symbol, "BUY", "call", long_call.contract.strike, long_call.contract.expiry.isoformat(), multiplier),
                            ),
                            entry_credit=total_credit,
                            fair_value=fair_credit,
                            max_loss_per_contract=max_loss,
                            target_debit=max(total_credit * (1.0 - strategy.target_capture_pct), 0.01),
                            stop_debit=max(total_credit * strategy.stop_multiple, 0.02),
                            breakeven_low=short_put.contract.strike - total_credit,
                            breakeven_high=short_call.contract.strike + total_credit,
                            net_delta_per_contract=net_delta,
                            net_gamma_per_contract=net_gamma,
                            net_vega_per_contract=net_vega,
                            net_theta_per_contract=net_theta,
                            metrics=CandidateMetrics(
                                expected_value=expected_value,
                                expected_value_after_costs=expected_value,
                                probability_of_profit=probability_of_profit,
                                probability_of_touch=probability_of_touch,
                                cvar_95=cvar_95,
                                return_on_risk=expected_value / max_loss,
                                liquidity_score=liquidity_score(short_put, long_put, short_call, long_call),
                                vol_regime_score=vol_regime_score,
                            ),
                            score=0.0,
                            thesis="harvest rich two-sided premium with capped wings",
                        )
                    )
    return candidates
