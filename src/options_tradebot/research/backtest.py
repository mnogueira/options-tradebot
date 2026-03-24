"""A simple event-driven backtester for option snapshots."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from options_tradebot.config.settings import AppSettings, default_settings
from options_tradebot.data.models import snapshots_from_frame
from options_tradebot.execution.service import PaperTradingService


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Backtest outputs."""

    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    output_dir: str

    def summary(self) -> dict[str, float]:
        if self.equity_curve.empty:
            return {
                "final_equity": 0.0,
                "return_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "trade_count": float(self.trades.shape[0]),
            }
        start_equity = float(self.equity_curve["equity"].iloc[0])
        final_equity = float(self.equity_curve["equity"].iloc[-1])
        curve = self.equity_curve["equity"]
        running_max = curve.cummax()
        drawdown = ((running_max - curve) / running_max.replace(0, pd.NA)).fillna(0.0)
        return {
            "final_equity": final_equity,
            "return_pct": 0.0 if start_equity <= 0 else (final_equity / start_equity - 1.0) * 100.0,
            "max_drawdown_pct": float(drawdown.max() * 100.0),
            "trade_count": float(self.trades.shape[0]),
        }


class OptionBacktester:
    """Backtest the paper service over a historical snapshot frame."""

    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or default_settings()

    def run(self, frame: pd.DataFrame, *, output_dir: str = "runtime/backtest") -> BacktestResult:
        """Run the backtest over all timestamps in the frame."""

        if frame.empty:
            raise ValueError("Backtest frame cannot be empty.")
        working = frame.copy()
        working["timestamp"] = pd.to_datetime(working["timestamp"])
        working = working.sort_values(["timestamp", "underlying", "symbol"]).reset_index(drop=True)
        service = PaperTradingService(settings=self.settings, output_dir=output_dir)
        history_buffer = pd.DataFrame(columns=working.columns)
        equity_points: list[dict[str, object]] = []
        for timestamp, slice_frame in working.groupby("timestamp"):
            history_buffer = pd.concat([history_buffer, slice_frame], ignore_index=True)
            underlying_histories = _underlying_histories_from_buffer(history_buffer)
            chain = snapshots_from_frame(history_buffer)
            step = service.run_once(chain, underlying_histories=underlying_histories)
            equity_points.append(
                {
                    "timestamp": timestamp,
                    "equity": step.equity,
                    "opened": step.opened,
                    "signal_action": step.signal.action,
                    "signal_reason": step.signal.reason,
                }
            )
        equity_curve = pd.DataFrame(equity_points)
        trades = pd.DataFrame([asdict(trade) for trade in service.broker.trades])
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        equity_curve.to_csv(directory / "equity_curve.csv", index=False)
        trades.to_csv(directory / "trades.csv", index=False)
        return BacktestResult(equity_curve=equity_curve, trades=trades, output_dir=str(directory))


def _underlying_histories_from_buffer(frame: pd.DataFrame) -> dict[str, pd.Series]:
    histories: dict[str, pd.Series] = {}
    for underlying, slice_frame in frame.groupby("underlying", dropna=False):
        ordered = (
            slice_frame.loc[:, ["timestamp", "underlying_price"]]
            .drop_duplicates(subset=["timestamp"], keep="last")
            .sort_values("timestamp")
        )
        histories[str(underlying)] = pd.Series(
            ordered["underlying_price"].astype(float).values,
            index=pd.to_datetime(ordered["timestamp"]),
        )
    return histories
