"""Paper broker for long-premium option trading."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import json
from pathlib import Path

from options_tradebot.market.models import OptionSnapshot
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
