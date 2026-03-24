"""Core option data models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from math import exp

import pandas as pd


class OptionKind(StrEnum):
    """Call or put."""

    CALL = "call"
    PUT = "put"


class UnderlyingType(StrEnum):
    """Spot-equity or futures-style underlying."""

    SPOT = "spot"
    FUTURE = "future"


@dataclass(frozen=True, slots=True)
class GreekVector:
    """Option Greeks per contract."""

    delta: float
    gamma: float
    vega: float
    theta: float


@dataclass(frozen=True, slots=True)
class OptionContract:
    """Contract metadata independent of the current quote."""

    symbol: str
    underlying: str
    option_type: OptionKind
    strike: float
    expiry: date
    underlying_type: UnderlyingType = UnderlyingType.SPOT
    contract_multiplier: int = 100
    exercise_style: str = "european"


@dataclass(frozen=True, slots=True)
class OptionQuote:
    """Current tradable quote."""

    bid: float
    ask: float
    last: float | None = None
    volume: int = 0
    open_interest: int | None = None

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.last or max(self.bid, self.ask, 0.0)

    @property
    def spread(self) -> float:
        return max(self.ask - self.bid, 0.0)

    @property
    def spread_pct(self) -> float:
        base = self.mid
        if base <= 0:
            return 1.0
        return self.spread / base


@dataclass(frozen=True, slots=True)
class OptionSnapshot:
    """Contract plus quote and contextual pricing inputs."""

    contract: OptionContract
    quote: OptionQuote
    timestamp: date | pd.Timestamp
    underlying_price: float
    risk_free_rate: float
    dividend_yield: float = 0.0
    implied_vol: float | None = None
    underlying_forward: float | None = None

    @property
    def time_to_expiry(self) -> float:
        days = max((pd.Timestamp(self.contract.expiry).date() - pd.Timestamp(self.timestamp).date()).days, 0)
        return days / 365.0

    @property
    def time_to_expiry_years(self) -> float:
        return self.time_to_expiry

    @property
    def dte(self) -> int:
        return max((pd.Timestamp(self.contract.expiry).date() - pd.Timestamp(self.timestamp).date()).days, 0)

    @property
    def days_to_expiry(self) -> int:
        return self.dte

    @property
    def mid_price(self) -> float:
        return self.quote.mid

    @property
    def ask_price(self) -> float:
        return self.quote.ask if self.quote.ask > 0 else self.quote.mid

    @property
    def bid_price(self) -> float:
        return self.quote.bid if self.quote.bid > 0 else self.quote.mid

    @property
    def premium_per_contract_ask(self) -> float:
        return self.ask_price * self.contract.contract_multiplier

    @property
    def premium_per_contract_bid(self) -> float:
        return self.bid_price * self.contract.contract_multiplier

    @property
    def liquidity_weight(self) -> float:
        spread_penalty = max(self.quote.spread_pct, 0.005)
        open_interest = 0 if self.quote.open_interest is None else self.quote.open_interest
        return (max(self.quote.volume, 1) + max(open_interest, 1)) / spread_penalty

    @property
    def forward_price(self) -> float:
        if self.underlying_forward is not None and self.underlying_forward > 0:
            return self.underlying_forward
        if self.contract.underlying_type == UnderlyingType.FUTURE:
            return self.underlying_price
        return self.underlying_price * exp(
            (self.risk_free_rate - self.dividend_yield) * self.time_to_expiry
        )
