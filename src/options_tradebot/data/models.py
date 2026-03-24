"""Snapshot dataset loaders."""

from __future__ import annotations

from datetime import date

import pandas as pd

from options_tradebot.market.models import (
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
        contract = OptionContract(
            symbol=str(row.symbol),
            underlying=str(row.underlying),
            option_type=OptionKind(str(row.option_type).lower()),
            strike=float(row.strike),
            expiry=_coerce_date(row.expiry),
            underlying_type=UnderlyingType(str(getattr(row, "underlying_type", "spot")).lower()),
            contract_multiplier=int(getattr(row, "contract_multiplier", 100)),
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
                risk_free_rate=float(getattr(row, "risk_free_rate", 0.14)),
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
