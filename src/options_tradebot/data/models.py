"""Snapshot dataset loaders."""

from __future__ import annotations

from datetime import date

import pandas as pd

from options_tradebot.market.models import (
    GreekVector,
    OptionContract,
    OptionKind,
    OptionQuote,
    OptionSnapshot,
    UnderlyingType,
)


def load_snapshot_csv(path: str) -> pd.DataFrame:
    """Load an option snapshot CSV into a normalized DataFrame."""

    frame = pd.read_csv(path)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"]).dt.date
    frame["expiry"] = pd.to_datetime(frame["expiry"]).dt.date
    return frame


def snapshots_from_frame(frame: pd.DataFrame) -> list[OptionSnapshot]:
    """Convert a snapshot frame into in-memory option snapshots."""

    snapshots: list[OptionSnapshot] = []
    for row in frame.itertuples(index=False):
        market = str(getattr(row, "market", "B3"))
        currency = str(getattr(row, "currency", "BRL"))
        risk_free_rate = getattr(row, "risk_free_rate", None)
        contract = OptionContract(
            symbol=str(row.symbol),
            underlying=str(row.underlying),
            option_type=OptionKind(str(row.option_type).lower()),
            strike=float(row.strike),
            expiry=_coerce_date(row.expiry),
            underlying_type=UnderlyingType(str(getattr(row, "underlying_type", "spot")).lower()),
            contract_multiplier=int(getattr(row, "contract_multiplier", 100)),
            exercise_style=str(getattr(row, "exercise_style", "european")),
            exchange=None if pd.isna(getattr(row, "exchange", None)) else str(row.exchange),
            currency=currency,
            contract_id=_coerce_optional_int(getattr(row, "contract_id", None)),
            local_symbol=(
                None if pd.isna(getattr(row, "local_symbol", None)) else str(row.local_symbol)
            ),
            trading_class=(
                None if pd.isna(getattr(row, "trading_class", None)) else str(row.trading_class)
            ),
        )
        quote = OptionQuote(
            bid=float(row.bid),
            ask=float(row.ask),
            last=None if pd.isna(getattr(row, "last", None)) else float(row.last),
            volume=int(getattr(row, "volume", 0)),
            open_interest=(
                None
                if pd.isna(getattr(row, "open_interest", None))
                else int(row.open_interest)
            ),
        )
        snapshots.append(
            OptionSnapshot(
                contract=contract,
                quote=quote,
                timestamp=_coerce_date(row.timestamp),
                underlying_price=float(row.underlying_price),
                risk_free_rate=_coerce_optional_float(
                    risk_free_rate,
                    default=0.045 if currency.upper() == "USD" or market.upper() == "US" else 0.14,
                ),
                dividend_yield=float(getattr(row, "dividend_yield", 0.0)),
                implied_vol=(
                    None
                    if pd.isna(getattr(row, "implied_vol", None))
                    else float(row.implied_vol)
                ),
                underlying_forward=(
                    None
                    if pd.isna(getattr(row, "underlying_forward", None))
                    else float(row.underlying_forward)
                ),
                market=market,
                broker_greeks=_coerce_broker_greeks(row),
            )
        )
    return snapshots


def _coerce_date(value: object) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if hasattr(value, "date") and not isinstance(value, date):
        return pd.Timestamp(value).date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def _coerce_optional_int(value: object) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _coerce_optional_float(value: object, *, default: float) -> float:
    if value is None or pd.isna(value):
        return float(default)
    return float(value)


def _coerce_broker_greeks(row: object) -> GreekVector | None:
    delta = getattr(row, "broker_delta", None)
    gamma = getattr(row, "broker_gamma", None)
    vega = getattr(row, "broker_vega", None)
    theta = getattr(row, "broker_theta", None)
    if any(pd.isna(value) for value in (delta, gamma, vega, theta)):
        return None
    if None in (delta, gamma, vega, theta):
        return None
    return GreekVector(
        delta=float(delta),
        gamma=float(gamma),
        vega=float(vega),
        theta=float(theta),
    )
