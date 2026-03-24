"""Paper-trading service orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

import pandas as pd

from options_tradebot.config.settings import AppSettings, default_settings
from options_tradebot.execution.paper import PaperBroker, PaperTrade
from options_tradebot.market.models import GreekVector, OptionSnapshot
from options_tradebot.market.surface import calibrate_surface
from options_tradebot.strategies.fair_value import FairValueOptionsStrategy, StrategySignal


@dataclass(frozen=True, slots=True)
class ServiceStepResult:
    """Single paper-trading step output."""

    timestamp: str
    signal: StrategySignal
    opened: bool
    closed_trades: tuple[PaperTrade, ...]
    equity: float
    state_path: str


class PaperTradingService:
    """Run the strategy against one timestamped option chain."""

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        broker: PaperBroker | None = None,
        output_dir: str | None = None,
    ):
        self.settings = settings or default_settings()
        self.broker = broker or PaperBroker(self.settings.paper.initial_cash)
        self.strategy = FairValueOptionsStrategy(self.settings)
        self.output_dir = output_dir or self.settings.paper.output_dir

    def run_once(self, snapshots: list[OptionSnapshot]) -> ServiceStepResult:
        """Run one service step for the provided chain."""

        if not snapshots:
            raise ValueError("Snapshots cannot be empty.")
        latest_by_symbol: dict[str, OptionSnapshot] = {}
        for snapshot in snapshots:
            latest_by_symbol[snapshot.contract.symbol] = snapshot
        live_snapshots = list(latest_by_symbol.values())
        live_snapshots.sort(key=lambda item: (item.contract.underlying, item.contract.symbol))
        timestamp = max(snapshot.timestamp for snapshot in live_snapshots).isoformat()
        by_underlying: dict[str, list[OptionSnapshot]] = {}
        for snapshot in live_snapshots:
            by_underlying.setdefault(snapshot.contract.underlying, []).append(snapshot)
        self.broker.mark_to_market(live_snapshots)
        closed_trades = tuple(
            self.broker.evaluate_exits(live_snapshots, self.settings.strategy.force_exit_dte)
        )
        best_signal = StrategySignal(
            action="HOLD",
            contract_symbol=None,
            underlying=None,
            contracts=0,
            entry_price=None,
            target_price=None,
            stop_price=None,
            fair_value=None,
            fair_volatility=None,
            reason="no_signal",
            score=0.0,
        )
        for underlying, chain in by_underlying.items():
            if (
                self.broker.has_underlying_position(underlying)
                and not self.settings.paper.allow_same_underlying_overlap
            ):
                continue
            surface, _ = calibrate_surface(chain)
            history = _underlying_history_from_chain(
                [
                    snapshot
                    for snapshot in snapshots
                    if snapshot.contract.underlying == underlying
                ]
            )
            aggregate_greeks = self.aggregate_greeks()
            signal = self.strategy.select_signal(
                chain=chain,
                underlying_history=history,
                account_equity=self.broker.equity(),
                current_portfolio_greeks=aggregate_greeks,
                surface=surface,
            )
            if signal.score > best_signal.score:
                best_signal = signal
        opened = False
        if best_signal.action != "HOLD" and best_signal.contract_symbol is not None:
            snapshot_map = {snapshot.contract.symbol: snapshot for snapshot in live_snapshots}
            opened = self.broker.open_position(best_signal, snapshot_map[best_signal.contract_symbol])
        state_path = self.persist_step(timestamp, best_signal, closed_trades)
        return ServiceStepResult(
            timestamp=timestamp,
            signal=best_signal,
            opened=opened,
            closed_trades=closed_trades,
            equity=self.broker.equity(),
            state_path=str(state_path),
        )

    def aggregate_greeks(self) -> GreekVector | None:
        """Aggregate approximate portfolio Greeks from open positions."""

        if not self.broker.positions:
            return None
        delta = 0.0
        gamma = 0.0
        vega = 0.0
        theta = 0.0
        for position in self.broker.positions.values():
            notional = position.contracts * position.contract_multiplier
            move_ratio = 0.5 if position.current_mark < position.entry_price else 0.8
            delta += move_ratio * notional
            gamma += 0.01 * position.contracts
            vega += 5.0 * position.contracts
            theta -= 1.0 * position.contracts
        return GreekVector(delta=delta, gamma=gamma, vega=vega, theta=theta)

    def persist_step(
        self,
        timestamp: str,
        signal: StrategySignal,
        closed_trades: tuple[PaperTrade, ...],
    ) -> Path:
        """Persist signals and state for observability."""

        output = Path(self.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        signal_log = output / "signals.jsonl"
        payload = {
            "timestamp": timestamp,
            "signal": asdict(signal),
            "closed_trades": [asdict(trade) for trade in closed_trades],
            "equity": self.broker.equity(),
        }
        with signal_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
        return self.broker.save_state(self.output_dir)


def _underlying_history_from_chain(chain: list[OptionSnapshot]) -> pd.Series:
    frame = pd.DataFrame(
        {
            "timestamp": [snapshot.timestamp for snapshot in chain],
            "underlying_price": [snapshot.underlying_price for snapshot in chain],
        }
    ).drop_duplicates(subset=["timestamp"], keep="last")
    frame = frame.sort_values("timestamp")
    return pd.Series(frame["underlying_price"].values, index=pd.to_datetime(frame["timestamp"]))
