"""Thin MetaTrader 5 wrapper for probing and data collection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from options_tradebot.market.models import (
    OptionContract,
    OptionKind,
    OptionQuote,
    OptionSnapshot,
    UnderlyingType,
)


@dataclass(frozen=True, slots=True)
class MT5ConnectionConfig:
    """MT5 initialization parameters."""

    path: str | None = None
    login: int | None = None
    password: str | None = None
    server: str | None = None
    timeout_ms: int = 60_000


class MT5MarketDataClient:
    """A safe MT5 client that can operate in probe-only mode."""

    def __init__(self, config: MT5ConnectionConfig):
        self.config = config
        self._mt5: Any | None = None
        self._connected = False

    def connect(self) -> dict[str, object]:
        """Connect to MT5 and return terminal/account metadata."""

        mt5 = self._import_mt5()
        kwargs: dict[str, object] = {"timeout": self.config.timeout_ms}
        if self.config.path:
            kwargs["path"] = self.config.path
        if self.config.login is not None:
            kwargs["login"] = self.config.login
        if self.config.password:
            kwargs["password"] = self.config.password
        if self.config.server:
            kwargs["server"] = self.config.server
        if not mt5.initialize(**kwargs):
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        self._mt5 = mt5
        self._connected = True
        return self.probe()

    def shutdown(self) -> None:
        if self._mt5 is not None:
            self._mt5.shutdown()
        self._connected = False
        self._mt5 = None

    def probe(self) -> dict[str, object]:
        """Return terminal and account diagnostics."""

        mt5 = self._require_connected()
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        return {
            "terminal": None if terminal is None else terminal._asdict(),
            "account": None if account is None else account._asdict(),
            "symbols_total": mt5.symbols_total(),
            "version": mt5.version(),
            "connected_at": datetime.now().isoformat(),
        }

    def list_symbols(self, group: str = "*") -> list[str]:
        """List symbol names visible to MT5."""

        mt5 = self._require_connected()
        raw = mt5.symbols_get(group) or []
        return [str(item.name) for item in raw]

    def fetch_bars(
        self,
        *,
        symbol: str,
        timeframe: str = "M5",
        count: int = 500,
    ) -> pd.DataFrame:
        """Download recent bars for a symbol."""

        mt5 = self._require_connected()
        timeframe_value = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1,
            "D1": mt5.TIMEFRAME_D1,
        }[timeframe.upper()]
        rates = mt5.copy_rates_from_pos(symbol, timeframe_value, 0, count)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No bars returned for {symbol}")
        frame = pd.DataFrame(rates)
        frame["time"] = pd.to_datetime(frame["time"], unit="s")
        return frame.rename(
            columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "tick_volume": "Volume",
            }
        ).set_index("time")

    def latest_tick(self, symbol: str) -> dict[str, float]:
        """Return the latest tick for a symbol."""

        mt5 = self._require_connected()
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"Tick unavailable for {symbol}")
        return tick._asdict()

    def snapshots_from_mapping(self, mapping_csv: str) -> list[OptionSnapshot]:
        """Build option snapshots from a user-maintained MT5 symbol mapping file."""

        mt5 = self._require_connected()
        mapping = pd.read_csv(mapping_csv)
        snapshots: list[OptionSnapshot] = []
        today = datetime.now().date()
        for row in mapping.itertuples(index=False):
            option_symbol = str(row.symbol)
            underlying_symbol = str(getattr(row, "underlying_symbol", row.underlying))
            option_tick = mt5.symbol_info_tick(option_symbol)
            underlying_tick = mt5.symbol_info_tick(underlying_symbol)
            option_info = mt5.symbol_info(option_symbol)
            if option_tick is None or underlying_tick is None or option_info is None:
                continue
            contract = OptionContract(
                symbol=option_symbol,
                underlying=str(row.underlying),
                option_type=OptionKind(str(row.option_type).lower()),
                strike=float(row.strike),
                expiry=pd.Timestamp(row.expiry).date(),
                underlying_type=UnderlyingType(str(getattr(row, "underlying_type", "spot")).lower()),
                contract_multiplier=int(getattr(row, "contract_multiplier", 100)),
            )
            quote = OptionQuote(
                bid=float(option_tick.bid),
                ask=float(option_tick.ask),
                last=None if getattr(option_tick, "last", 0.0) == 0 else float(option_tick.last),
                volume=int(getattr(option_info, "session_deals", 0) or 0),
                open_interest=(
                    None
                    if getattr(option_info, "session_interest", None) is None
                    else int(option_info.session_interest)
                ),
            )
            snapshots.append(
                OptionSnapshot(
                    contract=contract,
                    quote=quote,
                    timestamp=today,
                    underlying_price=float(
                        underlying_tick.last or underlying_tick.bid or underlying_tick.ask
                    ),
                    risk_free_rate=float(getattr(row, "risk_free_rate", 0.14)),
                    dividend_yield=float(getattr(row, "dividend_yield", 0.0)),
                    underlying_forward=(
                        None
                        if pd.isna(getattr(row, "underlying_forward", None))
                        else float(row.underlying_forward)
                    ),
                )
            )
        return snapshots

    def _require_connected(self):
        if not self._connected or self._mt5 is None:
            raise RuntimeError("MT5 client is not connected.")
        return self._mt5

    @staticmethod
    def _import_mt5():
        import MetaTrader5 as mt5  # noqa: N813

        return mt5
