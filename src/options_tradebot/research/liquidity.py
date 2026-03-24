"""Liquidity summaries for option datasets."""

from __future__ import annotations

import pandas as pd


def summarize_liquidity(frame: pd.DataFrame) -> pd.DataFrame:
    """Summarize volume, open interest, and spread by underlying."""

    if frame.empty:
        return pd.DataFrame()
    summary = (
        frame.assign(
            spread=lambda value: value["ask"] - value["bid"],
            mid=lambda value: (value["ask"] + value["bid"]) / 2.0,
        )
        .assign(spread_pct=lambda value: value["spread"] / value["mid"].replace(0, pd.NA))
        .groupby("underlying", dropna=False)
        .agg(
            observations=("symbol", "count"),
            total_volume=("volume", "sum"),
            average_open_interest=("open_interest", "mean"),
            median_spread_pct=("spread_pct", "median"),
            average_implied_vol=("implied_vol", "mean"),
        )
        .sort_values(["total_volume", "average_open_interest"], ascending=False)
    )
    return summary.reset_index()
