"""Paper broker for long-premium option trading."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import json
from pathlib import Path

from options_tradebot.market.models import GreekVector, OptionSnapshot, UnderlyingType
from options_tradebot.market.pricing import black_scholes_greeks
from options_tradebot.strategies.fair_value import StrategySignal


@dataclass(frozen=True, slots=True)
class PaperPosition:
    """Open paper position."""

    symbol: str
    underlying: str
    contracts: int
    entry_date: date
    entry_price: float
    current_mark: float
    target_price: float
    stop_price: float
    expiry: date
    contract_multiplier: int
    current_greeks: GreekVector
    strategy_reason: str

    @property
    def market_value(self) -> float:
        return self.current_mark * self.contract_multiplier * self.contracts


@dataclass(frozen=True, slots=True)
class PaperTrade:
    """Executed paper trade."""

    symbol: str
    underlying: str
    contracts: int
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    pnl: float
    exit_reason: str


class PaperBroker:
    """Long-only premium broker with simple journaling."""

    def __init__(self, initial_cash: float):
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.positions: dict[str, PaperPosition] = {}
        self.trades: list[PaperTrade] = []

    def has_underlying_position(self, underlying: str) -> bool:
        return any(position.underlying == underlying for position in self.positions.values())

    def open_position(self, signal: StrategySignal, snapshot: OptionSnapshot) -> bool:
        """Open a long option position if enough cash exists."""

        if signal.contract_symbol is None or signal.entry_price is None or signal.contracts <= 0:
            return False
        total_cost = signal.entry_price * snapshot.contract.contract_multiplier * signal.contracts
        if total_cost > self.cash:
            return False
        self.cash -= total_cost
        current_greeks = _scale_greeks(
            signal.greeks or _snapshot_greeks(snapshot, implied_vol=signal.fair_volatility),
            factor=snapshot.contract.contract_multiplier * signal.contracts,
        )
        self.positions[signal.contract_symbol] = PaperPosition(
            symbol=snapshot.contract.symbol,
            underlying=snapshot.contract.underlying,
            contracts=signal.contracts,
            entry_date=snapshot.timestamp,
            entry_price=signal.entry_price,
            current_mark=snapshot.mid_price,
            target_price=signal.target_price or signal.entry_price,
            stop_price=signal.stop_price or signal.entry_price,
            expiry=snapshot.contract.expiry,
            contract_multiplier=snapshot.contract.contract_multiplier,
            current_greeks=current_greeks,
            strategy_reason=signal.reason,
        )
        return True

    def mark_to_market(self, snapshots: list[OptionSnapshot]) -> None:
        """Update open positions with the latest mid marks."""

        snapshot_map = {snapshot.contract.symbol: snapshot for snapshot in snapshots}
        for symbol, position in list(self.positions.items()):
            snapshot = snapshot_map.get(symbol)
            if snapshot is None:
                continue
            self.positions[symbol] = PaperPosition(
                symbol=position.symbol,
                underlying=position.underlying,
                contracts=position.contracts,
                entry_date=position.entry_date,
                entry_price=position.entry_price,
                current_mark=snapshot.mid_price,
                target_price=position.target_price,
                stop_price=position.stop_price,
                expiry=position.expiry,
                contract_multiplier=position.contract_multiplier,
                current_greeks=_scale_greeks(
                    _snapshot_greeks(snapshot),
                    factor=position.contract_multiplier * position.contracts,
                ),
                strategy_reason=position.strategy_reason,
            )

    def evaluate_exits(self, snapshots: list[OptionSnapshot], force_exit_dte: int) -> list[PaperTrade]:
        """Close positions that hit stop, target, or expiry proximity."""

        snapshot_map = {snapshot.contract.symbol: snapshot for snapshot in snapshots}
        closed: list[PaperTrade] = []
        for symbol, position in list(self.positions.items()):
            snapshot = snapshot_map.get(symbol)
            if snapshot is None:
                continue
            if snapshot.bid_price >= position.target_price:
                closed.append(self.close_position(snapshot, "target"))
            elif snapshot.bid_price <= position.stop_price:
                closed.append(self.close_position(snapshot, "stop"))
            elif snapshot.dte <= force_exit_dte:
                closed.append(self.close_position(snapshot, "expiry_window"))
        return closed

    def close_position(self, snapshot: OptionSnapshot, exit_reason: str) -> PaperTrade:
        """Close an open position."""

        position = self.positions.pop(snapshot.contract.symbol)
        exit_price = snapshot.bid_price
        proceeds = exit_price * position.contract_multiplier * position.contracts
        self.cash += proceeds
        entry_cost = position.entry_price * position.contract_multiplier * position.contracts
        pnl = proceeds - entry_cost
        trade = PaperTrade(
            symbol=position.symbol,
            underlying=position.underlying,
            contracts=position.contracts,
            entry_date=position.entry_date,
            exit_date=snapshot.timestamp,
            entry_price=position.entry_price,
            exit_price=exit_price,
            pnl=pnl,
            exit_reason=exit_reason,
        )
        self.trades.append(trade)
        return trade

    def equity(self) -> float:
        """Return cash plus current marked value of positions."""

        return self.cash + sum(position.market_value for position in self.positions.values())

    def portfolio_state(self) -> dict[str, object]:
        """Return serializable account state."""

        return {
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "equity": self.equity(),
            "open_positions": [asdict(position) for position in self.positions.values()],
            "closed_trades": [asdict(trade) for trade in self.trades],
        }

    def save_state(self, output_dir: str) -> Path:
        """Write current state to JSON."""

        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "paper_state.json"
        target.write_text(json.dumps(self.portfolio_state(), indent=2, default=str), encoding="utf-8")
        return target


def _snapshot_greeks(snapshot: OptionSnapshot, *, implied_vol: float | None = None) -> GreekVector:
    if snapshot.broker_greeks is not None:
        return snapshot.broker_greeks
    volatility = implied_vol
    if volatility is None or volatility <= 0:
        volatility = snapshot.implied_vol if snapshot.implied_vol is not None and snapshot.implied_vol > 0 else 0.20
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
        volatility=volatility,
        option_type=snapshot.contract.option_type,
    )


def _scale_greeks(greeks: GreekVector, *, factor: int) -> GreekVector:
    return GreekVector(
        delta=greeks.delta * factor,
        gamma=greeks.gamma * factor,
        vega=greeks.vega * factor,
        theta=greeks.theta * factor,
    )
