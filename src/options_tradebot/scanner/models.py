"""Data models for the dynamic mispricing scanner."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from options_tradebot.market.models import OptionKind


@dataclass(frozen=True, slots=True)
class OptionMispricingFinding:
    """One option contract flagged by the scanner."""

    symbol: str
    underlying: str
    option_type: OptionKind
    strike: float
    expiry: str
    implied_vol: float | None
    fair_volatility: float
    market_price: float
    fair_value: float
    edge_reais: float
    edge_pct: float
    spread_pct: float
    volume: int
    open_interest: int | None
    thesis: str
    score: float


@dataclass(frozen=True, slots=True)
class UnderlyingScanResult:
    """Cross-sectional scanner output for one underlying."""

    underlying: str
    latest_timestamp: str
    realized_volatility: float
    atm_implied_volatility: float | None
    iv_vs_realized_spread: float
    iv_rank: float
    iv_percentile: float
    put_call_skew: float
    skew_anomaly: float
    skew_alpha_score: float
    volume_open_interest_ratio: float
    volume_open_interest_spike: float
    median_spread_pct: float
    bid_ask_quality: float
    next_event_days: int | None
    event_proximity_score: float
    best_option_edge_pct: float
    composite_score: float
    surface_method: str
    top_options: tuple[OptionMispricingFinding, ...]


def scan_results_to_frame(results: list[UnderlyingScanResult]) -> pd.DataFrame:
    """Flatten scanner results into a report-friendly DataFrame."""

    rows: list[dict[str, object]] = []
    for result in results:
        row = asdict(result)
        top_options = row.pop("top_options", ())
        row["top_option_symbols"] = ",".join(option["symbol"] for option in top_options)
        row["top_option_edges_pct"] = ",".join(
            f"{float(option['edge_pct']) * 100.0:.2f}" for option in top_options
        )
        rows.append(row)
    return pd.DataFrame(rows)
