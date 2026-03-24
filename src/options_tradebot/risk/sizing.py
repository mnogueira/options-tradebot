"""Position sizing for small-capital option trading."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor

from options_tradebot.market.models import GreekVector


@dataclass(frozen=True, slots=True)
class GreekLimits:
    """Aggregate portfolio Greek limits."""

    max_abs_delta: float = 250.0
    max_abs_gamma: float = 15.0
    max_abs_vega: float = 500.0


@dataclass(frozen=True, slots=True)
class PositionSizingDecision:
    """Sizing decision for one candidate trade."""

    contracts: int
    premium_budget: float
    rejected: bool
    reason: str


def _greek_capacity(
    *,
    current: GreekVector | None,
    candidate: GreekVector | None,
    limits: GreekLimits,
) -> int:
    if candidate is None:
        return 10_000
    current_vector = current or GreekVector(delta=0.0, gamma=0.0, vega=0.0, theta=0.0)
    capacities: list[int] = []
    for current_value, candidate_value, limit in (
        (current_vector.delta, candidate.delta, limits.max_abs_delta),
        (current_vector.gamma, candidate.gamma, limits.max_abs_gamma),
        (current_vector.vega, candidate.vega, limits.max_abs_vega),
    ):
        if abs(candidate_value) < 1e-9:
            capacities.append(10_000)
            continue
        remaining = max(limit - abs(current_value), 0.0)
        capacities.append(int(floor(remaining / abs(candidate_value))))
    return max(min(capacities), 0)


def size_option_position(
    *,
    account_equity: float,
    premium: float,
    contract_multiplier: int,
    risk_per_trade_pct: float,
    max_contracts: int,
    greek_limits: GreekLimits,
    current_portfolio_greeks: GreekVector | None = None,
    candidate_greeks: GreekVector | None = None,
) -> PositionSizingDecision:
    """Size a single option position for a small account."""

    if premium <= 0:
        return PositionSizingDecision(
            contracts=0,
            premium_budget=0.0,
            rejected=True,
            reason="non_positive_premium",
        )
    budget = max(account_equity * risk_per_trade_pct, 0.0)
    per_contract_cash = premium * contract_multiplier
    if per_contract_cash <= 0:
        return PositionSizingDecision(
            contracts=0,
            premium_budget=budget,
            rejected=True,
            reason="invalid_contract_cost",
        )
    affordability = int(floor(budget / per_contract_cash))
    greek_capacity = _greek_capacity(
        current=current_portfolio_greeks,
        candidate=candidate_greeks,
        limits=greek_limits,
    )
    contracts = min(affordability, greek_capacity, max_contracts)
    if contracts <= 0:
        return PositionSizingDecision(
            contracts=0,
            premium_budget=budget,
            rejected=True,
            reason="budget_or_greek_limit",
        )
    return PositionSizingDecision(
        contracts=contracts,
        premium_budget=budget,
        rejected=False,
        reason="approved",
    )
