"""Quote sanitation for venue-normalized option snapshots."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from options_tradebot.market.models import OptionQuote, OptionSnapshot


def sanitize_snapshot(snapshot: OptionSnapshot) -> OptionSnapshot:
    """Clamp invalid quotes into a conservative tradable representation."""

    bid = max(float(snapshot.quote.bid), 0.0)
    ask = max(float(snapshot.quote.ask), 0.0)
    last = None if snapshot.quote.last is None or float(snapshot.quote.last) <= 0 else float(snapshot.quote.last)
    if bid > 0 and ask > 0 and ask < bid:
        ask = bid
    quote = OptionQuote(
        bid=bid,
        ask=ask,
        last=last,
        volume=max(int(snapshot.quote.volume), 0),
        open_interest=None if snapshot.quote.open_interest is None else max(int(snapshot.quote.open_interest), 0),
    )
    return replace(snapshot, quote=quote)


def sanitize_snapshots(snapshots: Iterable[OptionSnapshot]) -> list[OptionSnapshot]:
    return [sanitize_snapshot(snapshot) for snapshot in snapshots]
