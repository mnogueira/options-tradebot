"""Thin MetaTrader 5 wrapper for probing and data collection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import log
from time import sleep
from typing import Any

import pandas as pd

from options_tradebot.market.models import (
    OptionContract,
    OptionKind,
    OptionQuote,
    OptionSnapshot,
    UnderlyingType,
)
from options_tradebot.market.pricing import implied_volatility


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

    def available_option_underlyings(
        self,
        *,
        option_path: str = "OPCOES",
    ) -> list[str]:
        """Return the sorted list of underlying symbols with listed options in MT5."""

        mt5 = self._require_connected()
        path_token = option_path.upper()
        underlyings = {
            str(getattr(item, "basis", "") or "").upper()
            for item in (mt5.symbols_get("*") or [])
            if path_token in str(getattr(item, "path", "") or "").upper()
            and str(getattr(item, "basis", "") or "").strip()
        }
        return sorted(value for value in underlyings if value)

    def collect_live_option_snapshots(
        self,
        *,
        underlyings: list[str] | None = None,
        dte_min: int = 3,
        dte_max: int = 25,
        max_expiries_per_underlying: int = 2,
        max_strikes_per_right: int = 12,
        moneyness_window: float | None = 0.20,
        risk_free_rate: float = 0.14,
        dividend_yield: float = 0.0,
        option_path: str = "OPCOES",
        selection_wait_seconds: float = 1.5,
    ) -> list[OptionSnapshot]:
        """Auto-discover and collect live B3 option snapshots directly from MT5."""

        mt5 = self._require_connected()
        path_token = option_path.upper()
        allowed_underlyings = None if underlyings is None else {value.upper() for value in underlyings}
        today = datetime.now().date()
        underlying_prices: dict[str, float] = {}
        grouped: dict[str, list[dict[str, object]]] = {}

        for info in mt5.symbols_get("*") or []:
            path = str(getattr(info, "path", "") or "").upper()
            underlying = str(getattr(info, "basis", "") or "").upper()
            if path_token not in path or not underlying:
                continue
            if allowed_underlyings is not None and underlying not in allowed_underlyings:
                continue
            expiry = _mt5_expiry_date(getattr(info, "expiration_time", 0))
            if expiry is None:
                continue
            dte = (expiry - today).days
            if dte < dte_min or dte > dte_max:
                continue
            strike = float(getattr(info, "option_strike", 0.0) or 0.0)
            if strike <= 0:
                continue
            if underlying not in underlying_prices:
                price = self._underlying_market_price(underlying)
                if price <= 0:
                    continue
                underlying_prices[underlying] = price
            spot = underlying_prices[underlying]
            if moneyness_window is not None and abs(log(max(strike, 1e-8) / max(spot, 1e-8))) > moneyness_window:
                continue
            grouped.setdefault(underlying, []).append(
                {
                    "symbol": str(getattr(info, "name", "")),
                    "underlying": underlying,
                    "expiry": expiry,
                    "dte": dte,
                    "strike": strike,
                    "option_right": int(getattr(info, "option_right", 0) or 0),
                    "strike_gap": abs(strike - spot),
                }
            )

        selected_symbols: list[str] = []
        for underlying, rows in grouped.items():
            expiries = sorted({row["expiry"] for row in rows})
            selected_expiries = expiries[:max_expiries_per_underlying] if max_expiries_per_underlying > 0 else expiries
            for expiry in selected_expiries:
                expiry_rows = [row for row in rows if row["expiry"] == expiry]
                by_right: dict[int, list[dict[str, object]]] = {}
                for row in expiry_rows:
                    by_right.setdefault(int(row["option_right"]), []).append(row)
                for right_rows in by_right.values():
                    right_rows.sort(key=lambda row: (float(row["strike_gap"]), str(row["symbol"])))
                    limit = max_strikes_per_right if max_strikes_per_right > 0 else len(right_rows)
                    selected_symbols.extend(str(row["symbol"]) for row in right_rows[:limit])

        unique_symbols = list(dict.fromkeys(selected_symbols))
        for symbol in unique_symbols:
            mt5.symbol_select(symbol, True)
        if unique_symbols and selection_wait_seconds > 0:
            sleep(selection_wait_seconds)

        snapshots: list[OptionSnapshot] = []
        for symbol in unique_symbols:
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            underlying = str(getattr(info, "basis", "") or "").upper()
            if not underlying:
                continue
            underlying_price = underlying_prices.get(underlying)
            if underlying_price is None or underlying_price <= 0:
                underlying_price = self._underlying_market_price(underlying)
                underlying_prices[underlying] = underlying_price
            if underlying_price <= 0:
                continue
            tick = mt5.symbol_info_tick(symbol)
            bid = _coerce_non_negative_float(None if tick is None else getattr(tick, "bid", None))
            ask = _coerce_non_negative_float(None if tick is None else getattr(tick, "ask", None))
            last = _coerce_optional_price(None if tick is None else getattr(tick, "last", None))
            if bid <= 0 and ask <= 0 and last is None:
                continue
            expiry = _mt5_expiry_date(getattr(info, "expiration_time", 0))
            if expiry is None:
                continue
            option_type = _mt5_option_type(getattr(info, "option_right", 0))
            quote = OptionQuote(
                bid=bid,
                ask=ask,
                last=last,
                volume=int(
                    max(
                        float(getattr(info, "session_deals", 0) or 0),
                        float(0.0 if tick is None else getattr(tick, "volume_real", 0.0) or 0.0),
                    )
                ),
                open_interest=_coerce_optional_interest(getattr(info, "session_interest", None)),
            )
            option_price = quote.mid if not pd.isna(quote.mid) else (quote.last or 0.0)
            timestamp = (
                datetime.now().date()
                if tick is None or int(getattr(tick, "time", 0) or 0) <= 0
                else pd.Timestamp(int(getattr(tick, "time", 0)), unit="s").date()
            )
            snapshots.append(
                OptionSnapshot(
                    contract=OptionContract(
                        symbol=symbol,
                        underlying=underlying,
                        option_type=option_type,
                        strike=float(getattr(info, "option_strike", 0.0) or 0.0),
                        expiry=expiry,
                        underlying_type=UnderlyingType.SPOT,
                        contract_multiplier=100,
                        exercise_style="american",
                        exchange=str(getattr(info, "exchange", "") or "BVMF"),
                        currency=str(getattr(info, "currency_profit", "") or "BRL"),
                    ),
                    quote=quote,
                    timestamp=timestamp,
                    underlying_price=underlying_price,
                    risk_free_rate=risk_free_rate,
                    dividend_yield=dividend_yield,
                    implied_vol=implied_volatility(
                        market_price=option_price,
                        spot=underlying_price,
                        strike=float(getattr(info, "option_strike", 0.0) or 0.0),
                        time_to_expiry=max((expiry - timestamp).days, 0) / 252.0,
                        rate=risk_free_rate,
                        dividend_yield=dividend_yield,
                        option_type=option_type,
                    ),
                    market="B3",
                )
            )
        return snapshots

    def _underlying_market_price(self, symbol: str) -> float:
        mt5 = self._require_connected()
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        for value in (
            None if info is None else getattr(info, "last", None),
            None if tick is None else getattr(tick, "last", None),
            None if tick is None else getattr(tick, "bid", None),
            None if tick is None else getattr(tick, "ask", None),
            None if info is None else getattr(info, "bid", None),
            None if info is None else getattr(info, "ask", None),
        ):
            if value is not None and float(value) > 0:
                return float(value)
        return 0.0

    def _require_connected(self):
        if not self._connected or self._mt5 is None:
            raise RuntimeError("MT5 client is not connected.")
        return self._mt5

    @staticmethod
    def _import_mt5():
        import MetaTrader5 as mt5  # noqa: N813

        return mt5


def _mt5_expiry_date(value: object):
    timestamp = int(value or 0)
    if timestamp <= 0:
        return None
    return pd.Timestamp(timestamp, unit="s").date()


def _mt5_option_type(value: object) -> OptionKind:
    return OptionKind.CALL if int(value or 0) == 0 else OptionKind.PUT


def _coerce_non_negative_float(value: object) -> float:
    if value is None:
        return 0.0
    return max(float(value), 0.0)


def _coerce_optional_price(value: object) -> float | None:
    if value is None:
        return None
    coerced = float(value)
    return None if coerced <= 0 else coerced


def _coerce_optional_interest(value: object) -> int | None:
    if value is None:
        return None
    coerced = int(float(value or 0))
    return None if coerced <= 0 else coerced
