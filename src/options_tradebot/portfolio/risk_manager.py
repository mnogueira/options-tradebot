"""Portfolio-aware sizing and candidate approval."""

from __future__ import annotations

from dataclasses import replace
from math import floor
from typing import Iterable

from options_tradebot.config.schema import RiskConfig
from options_tradebot.portfolio.state import PortfolioState
from options_tradebot.strategies.defined_risk.types import ManagedPosition, StrategyTradeCandidate


def approve_candidates(
    candidates: Iterable[StrategyTradeCandidate],
    *,
    risk: RiskConfig,
    portfolio_state: PortfolioState,
) -> list[StrategyTradeCandidate]:
    approved: list[StrategyTradeCandidate] = []
    provisional_positions = list(portfolio_state.open_positions)
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        sized = _size_candidate(candidate, risk=risk, current_positions=provisional_positions)
        if sized is None:
            continue
        approved.append(sized)
        provisional_positions.append(ManagedPosition.from_candidate(sized, mode="PENDING"))
    return approved


def _size_candidate(
    candidate: StrategyTradeCandidate,
    *,
    risk: RiskConfig,
    current_positions: list[ManagedPosition],
) -> StrategyTradeCandidate | None:
    if len(current_positions) >= risk.max_positions_total:
        return None
    if sum(position.venue == candidate.venue for position in current_positions) >= risk.max_positions_per_venue:
        return None
    if sum(position.underlying == candidate.underlying for position in current_positions) >= risk.max_positions_per_underlying:
        return None
    if candidate.max_loss_per_contract <= 0:
        return None

    current_capital_at_risk = sum(position.max_loss for position in current_positions if position.status == "OPEN")
    total_limit = risk.capital_base * risk.max_total_capital_at_risk_pct
    per_position_limit = risk.capital_base * risk.max_position_capital_at_risk_pct
    remaining_total_limit = max(total_limit - current_capital_at_risk, 0.0)
    max_allocatable = min(per_position_limit, remaining_total_limit)
    contracts = min(
        max(int(floor(max_allocatable / candidate.max_loss_per_contract)), 0),
        risk.max_contracts_per_trade,
    )
    if contracts <= 0:
        return None

    net_delta = candidate.net_delta_per_contract * contracts + sum(position.net_delta for position in current_positions)
    net_gamma = candidate.net_gamma_per_contract * contracts + sum(position.net_gamma for position in current_positions)
    net_vega = candidate.net_vega_per_contract * contracts + sum(position.net_vega for position in current_positions)
    if abs(net_delta) > risk.max_abs_delta:
        return None
    if abs(min(net_gamma, 0.0)) > risk.max_short_gamma:
        return None
    if abs(min(net_vega, 0.0)) > risk.max_short_vega:
        return None
    return candidate.with_contracts(contracts)
