"""Rank defined-risk short-vol candidates for one underlying chain."""

from __future__ import annotations

from dataclasses import replace
from math import log

import numpy as np
import pandas as pd

from options_tradebot.config.schema import ShortVolRuntimeConfig
from options_tradebot.market.models import OptionSnapshot
from options_tradebot.normalization import sanitize_snapshots
from options_tradebot.strategies.defined_risk.analytics import build_surface_and_anchor_vol
from options_tradebot.strategies.defined_risk.bear_call_spread import build_bear_call_candidates
from options_tradebot.strategies.defined_risk.bull_put_spread import build_bull_put_candidates
from options_tradebot.strategies.defined_risk.iron_condor import build_iron_condor_candidates
from options_tradebot.strategies.defined_risk.types import StrategyTradeCandidate


def rank_defined_risk_candidates(
    *,
    venue: str,
    chain: list[OptionSnapshot],
    underlying_history: pd.Series,
    config: ShortVolRuntimeConfig,
) -> list[StrategyTradeCandidate]:
    if not chain or underlying_history.empty:
        return []
    normalized_chain = sanitize_snapshots(chain)
    surface, anchor_vol, realized_vol, _forecast_vol = build_surface_and_anchor_vol(
        chain=normalized_chain,
        underlying_history=underlying_history,
        pricing=config.pricing,
    )
    atm_iv = _atm_implied_volatility(normalized_chain)
    vol_regime_score = max(atm_iv - anchor_vol, 0.0) if atm_iv is not None else 0.0
    candidates: list[StrategyTradeCandidate] = []
    if config.strategies.bull_put.enabled:
        candidates.extend(
            build_bull_put_candidates(
                venue=venue,
                chain=normalized_chain,
                anchor_vol=anchor_vol,
                vol_regime_score=vol_regime_score,
                liquidity=config.liquidity,
                pricing=config.pricing,
                strategy=config.strategies.bull_put,
                min_dte=config.discovery.mt5.dte_min if venue == "mt5" else config.discovery.ib.dte_min,
                max_dte=config.discovery.mt5.dte_max if venue == "mt5" else config.discovery.ib.dte_max,
            )
        )
    if config.strategies.bear_call.enabled:
        candidates.extend(
            build_bear_call_candidates(
                venue=venue,
                chain=normalized_chain,
                anchor_vol=anchor_vol,
                vol_regime_score=vol_regime_score,
                liquidity=config.liquidity,
                pricing=config.pricing,
                strategy=config.strategies.bear_call,
                min_dte=config.discovery.mt5.dte_min if venue == "mt5" else config.discovery.ib.dte_min,
                max_dte=config.discovery.mt5.dte_max if venue == "mt5" else config.discovery.ib.dte_max,
            )
        )
    if config.strategies.iron_condor.enabled:
        candidates.extend(
            build_iron_condor_candidates(
                venue=venue,
                chain=normalized_chain,
                anchor_vol=anchor_vol,
                vol_regime_score=vol_regime_score,
                liquidity=config.liquidity,
                pricing=config.pricing,
                strategy=config.strategies.iron_condor,
                min_dte=config.discovery.mt5.dte_min if venue == "mt5" else config.discovery.ib.dte_min,
                max_dte=config.discovery.mt5.dte_max if venue == "mt5" else config.discovery.ib.dte_max,
            )
        )
    ranked = [_score_candidate(candidate, config) for candidate in candidates if candidate.metrics.expected_value_after_costs > 0]
    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked[: config.ranking.top_candidates_per_cycle]


def _score_candidate(candidate: StrategyTradeCandidate, config: ShortVolRuntimeConfig) -> StrategyTradeCandidate:
    weights = config.ranking.weights
    expected_value_score = np.tanh(candidate.metrics.expected_value_after_costs / max(candidate.max_loss_per_contract, 1e-8))
    return_on_risk_score = np.tanh(candidate.metrics.return_on_risk / 0.20)
    cvar_penalty_score = np.tanh(candidate.metrics.cvar_95 / max(candidate.max_loss_per_contract, 1e-8))
    liquidity_score = np.tanh(candidate.metrics.liquidity_score / 3.0)
    vol_regime_score = np.tanh(candidate.metrics.vol_regime_score / 0.10)
    composite = (
        weights.expected_value * expected_value_score
        + weights.return_on_risk * return_on_risk_score
        + weights.probability_of_profit * candidate.metrics.probability_of_profit
        - weights.cvar_penalty * cvar_penalty_score
        + weights.liquidity * liquidity_score
        + weights.vol_regime * vol_regime_score
    )
    return replace(candidate, score=float(composite))


def _atm_implied_volatility(chain: list[OptionSnapshot]) -> float | None:
    eligible = [snapshot for snapshot in chain if snapshot.implied_vol is not None and snapshot.implied_vol > 0 and snapshot.time_to_expiry > 0]
    if not eligible:
        return None
    ranked = sorted(
        eligible,
        key=lambda snapshot: (
            abs(log(max(snapshot.contract.strike, 1e-8) / max(snapshot.forward_price, 1e-8))),
            snapshot.quote.spread_pct,
        ),
    )
    sample = ranked[: min(4, len(ranked))]
    return float(np.median([float(snapshot.implied_vol or 0.0) for snapshot in sample]))
