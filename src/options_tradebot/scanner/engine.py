"""Cross-sectional scanner for B3 options mispricing candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite, log
from typing import Sequence

import numpy as np
import pandas as pd

from options_tradebot.config import AppSettings, default_settings
from options_tradebot.market import (
    OptionKind,
    OptionSnapshot,
    UnderlyingType,
    annualized_realized_volatility,
    black_76_price,
    black_scholes_greeks,
    black_scholes_price,
    calibrate_surface,
    garch11_forecast_volatility,
)


@dataclass(frozen=True, slots=True)
class OptionMispricingRecord:
    """One actionable option highlight from the scanner."""

    symbol: str
    underlying: str
    option_type: str
    expiry: date
    strike: float
    dte: int
    quoted_price: float
    fair_value: float
    implied_vol: float | None
    fair_volatility: float
    edge_pct: float
    action_hint: str
    spread_pct: float
    volume: int
    open_interest: int | None
    tradeable: bool


@dataclass(frozen=True, slots=True)
class UnderlyingScanMetrics:
    """Cross-sectional features for one underlying."""

    current_atm_iv: float
    realized_vol: float
    forecast_vol: float
    iv_spread: float
    iv_rank: float
    iv_percentile: float
    put_call_skew: float
    skew_zscore: float
    volume_oi_ratio: float
    volume_oi_zscore: float
    median_spread_pct: float
    liquidity_score: float
    days_to_event: int | None
    event_score: float
    top_option_edge_pct: float


@dataclass(frozen=True, slots=True)
class ScanCandidate:
    """Ranked scanner result for one underlying."""

    underlying: str
    as_of: date
    score: float
    metrics: UnderlyingScanMetrics
    highlights: tuple[OptionMispricingRecord, ...]


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Full scanner output."""

    as_of: date
    candidates: tuple[ScanCandidate, ...]

    def to_frame(self, *, top_n: int | None = None) -> pd.DataFrame:
        """Return scanner rankings as a tabular summary."""

        rows: list[dict[str, object]] = []
        for rank, candidate in enumerate(self.top_candidates(top_n), start=1):
            rows.append(
                {
                    "rank": rank,
                    "as_of": candidate.as_of.isoformat(),
                    "underlying": candidate.underlying,
                    "score": candidate.score,
                    "current_atm_iv": candidate.metrics.current_atm_iv,
                    "realized_vol": candidate.metrics.realized_vol,
                    "forecast_vol": candidate.metrics.forecast_vol,
                    "iv_spread": candidate.metrics.iv_spread,
                    "iv_rank": candidate.metrics.iv_rank,
                    "iv_percentile": candidate.metrics.iv_percentile,
                    "put_call_skew": candidate.metrics.put_call_skew,
                    "skew_zscore": candidate.metrics.skew_zscore,
                    "volume_oi_ratio": candidate.metrics.volume_oi_ratio,
                    "volume_oi_zscore": candidate.metrics.volume_oi_zscore,
                    "median_spread_pct": candidate.metrics.median_spread_pct,
                    "liquidity_score": candidate.metrics.liquidity_score,
                    "days_to_event": candidate.metrics.days_to_event,
                    "event_score": candidate.metrics.event_score,
                    "top_option_edge_pct": candidate.metrics.top_option_edge_pct,
                    "highlight_symbols": ",".join(item.symbol for item in candidate.highlights),
                }
            )
        return pd.DataFrame(rows)

    def highlights_frame(self, *, top_n: int | None = None) -> pd.DataFrame:
        """Return one row per highlighted option."""

        rows: list[dict[str, object]] = []
        for rank, candidate in enumerate(self.top_candidates(top_n), start=1):
            for item in candidate.highlights:
                row = asdict(item)
                row["rank"] = rank
                row["as_of"] = candidate.as_of.isoformat()
                rows.append(row)
        return pd.DataFrame(rows)

    def top_candidates(self, top_n: int | None = None) -> tuple[ScanCandidate, ...]:
        """Return the leading underlyings."""

        if top_n is None or top_n >= len(self.candidates):
            return self.candidates
        return self.candidates[:top_n]


class MispricingScanner:
    """Rank underlyings by how likely they are to host option mispricings now."""

    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or default_settings()

    def scan(
        self,
        snapshots: Sequence[OptionSnapshot],
        *,
        event_calendar: pd.DataFrame | None = None,
    ) -> ScanResult:
        """Scan all underlyings represented in the snapshot set."""

        if not snapshots:
            raise ValueError("Scanner requires at least one option snapshot.")
        grouped: dict[str, list[OptionSnapshot]] = {}
        for snapshot in snapshots:
            grouped.setdefault(snapshot.contract.underlying, []).append(snapshot)
        candidates = [
            candidate
            for candidate in (
                self._scan_underlying(underlying, chain, event_calendar=event_calendar)
                for underlying, chain in grouped.items()
            )
            if candidate is not None
        ]
        candidates.sort(key=lambda item: item.score, reverse=True)
        as_of = max(pd.Timestamp(snapshot.timestamp).date() for snapshot in snapshots)
        return ScanResult(as_of=as_of, candidates=tuple(candidates))

    def _scan_underlying(
        self,
        underlying: str,
        snapshots: list[OptionSnapshot],
        *,
        event_calendar: pd.DataFrame | None,
    ) -> ScanCandidate | None:
        latest_timestamp = max(pd.Timestamp(snapshot.timestamp).date() for snapshot in snapshots)
        current_chain = [
            snapshot
            for snapshot in snapshots
            if pd.Timestamp(snapshot.timestamp).date() == latest_timestamp
        ]
        if not current_chain:
            return None
        current_chain.sort(key=lambda item: (item.contract.expiry, item.contract.option_type, item.contract.strike))
        history = _underlying_history_from_snapshots(snapshots)
        realized_vol, forecast_vol = _vol_forecasts(
            history,
            lookback=self.settings.scanner.realized_vol_lookback,
            alpha=self.settings.strategy.garch_alpha,
            beta=self.settings.strategy.garch_beta,
        )
        current_atm_iv, iv_rank, iv_percentile = _atm_iv_features(
            snapshots,
            target_dte=self.settings.scanner.atm_target_dte,
            history_lookback=self.settings.scanner.iv_history_lookback,
        )
        put_call_skew, skew_zscore = _skew_features(snapshots)
        volume_oi_ratio, volume_oi_zscore = _flow_features(snapshots)
        median_spread_pct = float(
            np.median([snapshot.quote.spread_pct for snapshot in current_chain if snapshot.quote.mid > 0])
        )
        liquidity_score = float(
            np.clip(
                1.0 - median_spread_pct / max(self.settings.universe.max_spread_pct, 1e-8),
                0.0,
                1.0,
            )
        )
        days_to_event, event_score = _event_features(
            underlying,
            latest_timestamp,
            event_calendar,
            window_days=self.settings.scanner.event_window_days,
        )
        highlights = self._highlight_options(
            current_chain,
            underlying_history=history,
        )
        top_option_edge_pct = 0.0 if not highlights else max(abs(item.edge_pct) for item in highlights)
        metrics = UnderlyingScanMetrics(
            current_atm_iv=current_atm_iv,
            realized_vol=realized_vol,
            forecast_vol=forecast_vol,
            iv_spread=current_atm_iv - realized_vol,
            iv_rank=iv_rank,
            iv_percentile=iv_percentile,
            put_call_skew=put_call_skew,
            skew_zscore=skew_zscore,
            volume_oi_ratio=volume_oi_ratio,
            volume_oi_zscore=volume_oi_zscore,
            median_spread_pct=median_spread_pct,
            liquidity_score=liquidity_score,
            days_to_event=days_to_event,
            event_score=event_score,
            top_option_edge_pct=top_option_edge_pct,
        )
        score = self._score_candidate(metrics)
        return ScanCandidate(
            underlying=underlying,
            as_of=latest_timestamp,
            score=score,
            metrics=metrics,
            highlights=tuple(highlights[: self.settings.scanner.top_option_count]),
        )

    def _highlight_options(
        self,
        chain: Sequence[OptionSnapshot],
        *,
        underlying_history: pd.Series,
    ) -> list[OptionMispricingRecord]:
        universe = self.settings.universe
        returns = np.log(underlying_history.astype(float)).diff().dropna().tail(
            self.settings.strategy.realized_vol_lookback
        )
        realized_vol = annualized_realized_volatility(returns)
        forecast_vol = garch11_forecast_volatility(
            returns,
            alpha=self.settings.strategy.garch_alpha,
            beta=self.settings.strategy.garch_beta,
        )
        surface, _ = calibrate_surface(chain)
        highlights: list[OptionMispricingRecord] = []
        for snapshot in chain:
            if snapshot.ask_price <= 0 or snapshot.bid_price < 0:
                continue
            surface_vol = surface.volatility_for_snapshot(snapshot)
            forecast_component = max(realized_vol, forecast_vol, 0.05)
            fair_vol = max(
                self.settings.strategy.fair_vol_surface_weight * surface_vol
                + self.settings.strategy.fair_vol_forecast_weight * forecast_component,
                0.05,
            )
            if snapshot.contract.underlying_type == UnderlyingType.FUTURE:
                fair_value = black_76_price(
                    forward=snapshot.forward_price,
                    strike=snapshot.contract.strike,
                    time_to_expiry=snapshot.time_to_expiry,
                    rate=snapshot.risk_free_rate,
                    volatility=fair_vol,
                    option_type=snapshot.contract.option_type,
                )
            else:
                fair_value = black_scholes_price(
                    spot=snapshot.underlying_price,
                    strike=snapshot.contract.strike,
                    time_to_expiry=snapshot.time_to_expiry,
                    rate=snapshot.risk_free_rate,
                    dividend_yield=snapshot.dividend_yield,
                    volatility=fair_vol,
                    option_type=snapshot.contract.option_type,
                )
            buy_edge_pct = (fair_value - snapshot.ask_price) / max(snapshot.ask_price, 1e-8)
            bid_reference = snapshot.bid_price if snapshot.bid_price > 0 else snapshot.mid_price
            sell_edge_pct = (bid_reference - fair_value) / max(bid_reference, 1e-8)
            if buy_edge_pct >= sell_edge_pct:
                edge_pct = buy_edge_pct
                quoted_price = snapshot.ask_price
                action_hint = "buy_cheap" if edge_pct > 0 else "avoid"
            else:
                edge_pct = sell_edge_pct
                quoted_price = bid_reference
                action_hint = "sell_rich" if edge_pct > 0 else "avoid"
            tradeable = (
                snapshot.dte >= universe.min_dte
                and snapshot.dte <= universe.max_dte
                and snapshot.quote.volume >= universe.min_daily_volume
                and (snapshot.quote.open_interest or 0) >= universe.min_open_interest
                and snapshot.quote.spread_pct <= universe.max_spread_pct
            )
            highlights.append(
                OptionMispricingRecord(
                    symbol=snapshot.contract.symbol,
                    underlying=snapshot.contract.underlying,
                    option_type=snapshot.contract.option_type.value,
                    expiry=snapshot.contract.expiry,
                    strike=snapshot.contract.strike,
                    dte=snapshot.dte,
                    quoted_price=quoted_price,
                    fair_value=fair_value,
                    implied_vol=snapshot.implied_vol,
                    fair_volatility=fair_vol,
                    edge_pct=edge_pct,
                    action_hint=action_hint,
                    spread_pct=snapshot.quote.spread_pct,
                    volume=snapshot.quote.volume,
                    open_interest=snapshot.quote.open_interest,
                    tradeable=tradeable,
                )
            )
        highlights.sort(
            key=lambda item: (item.tradeable, abs(item.edge_pct), item.volume),
            reverse=True,
        )
        return highlights

    def _score_candidate(self, metrics: UnderlyingScanMetrics) -> float:
        scanner = self.settings.scanner
        iv_spread_score = _positive_squash(metrics.iv_spread, scale=0.05)
        iv_rank_score = float(np.clip((metrics.iv_rank + metrics.iv_percentile) / 2.0, 0.0, 1.0))
        skew_input = metrics.skew_zscore if isfinite(metrics.skew_zscore) and metrics.skew_zscore != 0 else metrics.put_call_skew
        skew_score = _absolute_squash(skew_input, scale=1.5 if abs(skew_input) > 1.0 else 0.06)
        flow_input = metrics.volume_oi_zscore if isfinite(metrics.volume_oi_zscore) and metrics.volume_oi_zscore != 0 else metrics.volume_oi_ratio
        flow_score = _positive_squash(flow_input, scale=1.0)
        liquidity_score = float(np.clip(metrics.liquidity_score, 0.0, 1.0))
        event_score = float(np.clip(metrics.event_score, 0.0, 1.0))
        option_edge_score = _absolute_squash(metrics.top_option_edge_pct, scale=0.12)
        composite = (
            scanner.weight_iv_spread * iv_spread_score
            + scanner.weight_iv_rank * iv_rank_score
            + scanner.weight_skew * skew_score
            + scanner.weight_flow * flow_score
            + scanner.weight_liquidity * liquidity_score
            + scanner.weight_event * event_score
            + scanner.weight_option_edge * option_edge_score
        )
        return round(composite * 100.0, 4)


def _atm_iv_features(
    snapshots: Sequence[OptionSnapshot],
    *,
    target_dte: int,
    history_lookback: int,
) -> tuple[float, float, float]:
    grouped: dict[date, list[OptionSnapshot]] = {}
    for snapshot in snapshots:
        grouped.setdefault(pd.Timestamp(snapshot.timestamp).date(), []).append(snapshot)
    if not grouped:
        return 0.0, 0.5, 0.5
    ordered_dates = sorted(grouped)
    iv_series: list[float] = []
    for timestamp in ordered_dates:
        atm = _select_atm_snapshot(grouped[timestamp], target_dte=target_dte)
        if atm is not None and atm.implied_vol is not None:
            iv_series.append(float(atm.implied_vol))
    if not iv_series:
        return 0.0, 0.5, 0.5
    history = iv_series[-history_lookback:]
    current = history[-1]
    prior = history[:-1]
    if not prior:
        return current, 0.5, 0.5
    low = min(prior)
    high = max(prior)
    iv_rank = 0.5 if high <= low else (current - low) / (high - low)
    iv_percentile = float(np.mean(np.asarray(prior, dtype=float) <= current))
    return current, float(np.clip(iv_rank, 0.0, 1.0)), float(np.clip(iv_percentile, 0.0, 1.0))


def _select_atm_snapshot(
    chain: Sequence[OptionSnapshot],
    *,
    target_dte: int,
) -> OptionSnapshot | None:
    eligible = [snapshot for snapshot in chain if snapshot.implied_vol is not None and snapshot.time_to_expiry > 0]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda snapshot: (
            abs(snapshot.dte - target_dte),
            abs(log(max(snapshot.contract.strike, 1e-8) / max(snapshot.forward_price, 1e-8))),
            snapshot.quote.spread_pct,
        ),
    )


def _skew_features(snapshots: Sequence[OptionSnapshot]) -> tuple[float, float]:
    grouped: dict[date, list[OptionSnapshot]] = {}
    for snapshot in snapshots:
        grouped.setdefault(pd.Timestamp(snapshot.timestamp).date(), []).append(snapshot)
    if not grouped:
        return 0.0, 0.0
    values: list[float] = []
    for timestamp in sorted(grouped):
        value = _chain_skew(grouped[timestamp])
        if value is not None:
            values.append(value)
    if not values:
        return 0.0, 0.0
    current = values[-1]
    prior = values[:-1]
    if len(prior) < 2:
        return current, 0.0
    baseline = float(np.mean(prior))
    dispersion = float(np.std(prior, ddof=0))
    if dispersion <= 1e-8:
        return current, 0.0
    return current, (current - baseline) / dispersion


def _chain_skew(chain: Sequence[OptionSnapshot]) -> float | None:
    calls = [snapshot for snapshot in chain if snapshot.contract.option_type == OptionKind.CALL]
    puts = [snapshot for snapshot in chain if snapshot.contract.option_type == OptionKind.PUT]
    put_candidate = _closest_delta_snapshot(puts, target_delta=-0.25)
    call_candidate = _closest_delta_snapshot(calls, target_delta=0.25)
    if put_candidate is None or call_candidate is None:
        return None
    if put_candidate.implied_vol is None or call_candidate.implied_vol is None:
        return None
    return float(put_candidate.implied_vol - call_candidate.implied_vol)


def _closest_delta_snapshot(
    chain: Sequence[OptionSnapshot],
    *,
    target_delta: float,
) -> OptionSnapshot | None:
    eligible: list[tuple[float, OptionSnapshot]] = []
    for snapshot in chain:
        if snapshot.implied_vol is None or snapshot.time_to_expiry <= 0:
            continue
        greeks = black_scholes_greeks(
            spot=snapshot.underlying_price,
            strike=snapshot.contract.strike,
            time_to_expiry=snapshot.time_to_expiry,
            rate=snapshot.risk_free_rate,
            dividend_yield=snapshot.dividend_yield,
            volatility=max(snapshot.implied_vol, 0.05),
            option_type=snapshot.contract.option_type,
        )
        eligible.append((abs(greeks.delta - target_delta), snapshot))
    if not eligible:
        return None
    return min(eligible, key=lambda item: item[0])[1]


def _flow_features(snapshots: Sequence[OptionSnapshot]) -> tuple[float, float]:
    grouped: dict[date, list[OptionSnapshot]] = {}
    for snapshot in snapshots:
        grouped.setdefault(pd.Timestamp(snapshot.timestamp).date(), []).append(snapshot)
    ratios = []
    for timestamp in sorted(grouped):
        chain = grouped[timestamp]
        volume = float(sum(snapshot.quote.volume for snapshot in chain))
        open_interest = float(sum(snapshot.quote.open_interest or 0 for snapshot in chain))
        ratios.append(volume / max(open_interest, 1.0))
    if not ratios:
        return 0.0, 0.0
    current = ratios[-1]
    prior = ratios[:-1]
    if len(prior) < 2:
        return current, 0.0
    baseline = float(np.mean(prior))
    dispersion = float(np.std(prior, ddof=0))
    if dispersion <= 1e-8:
        return current, 0.0
    return current, (current - baseline) / dispersion


def _vol_forecasts(
    history: pd.Series,
    *,
    lookback: int,
    alpha: float,
    beta: float,
) -> tuple[float, float]:
    returns = np.log(history.astype(float)).diff().dropna().tail(lookback)
    if returns.empty:
        return 0.0, 0.0
    realized = annualized_realized_volatility(returns)
    forecast = garch11_forecast_volatility(returns, alpha=alpha, beta=beta)
    return realized, forecast


def _event_features(
    underlying: str,
    as_of: date,
    event_calendar: pd.DataFrame | None,
    *,
    window_days: int,
) -> tuple[int | None, float]:
    if event_calendar is None or event_calendar.empty:
        return None, 0.0
    frame = event_calendar.copy()
    if "underlying" not in frame.columns or "event_date" not in frame.columns:
        return None, 0.0
    frame = frame.loc[frame["underlying"].astype(str) == underlying]
    if frame.empty:
        return None, 0.0
    frame["event_date"] = pd.to_datetime(frame["event_date"]).dt.date
    frame["days_to_event"] = frame["event_date"].map(lambda value: (value - as_of).days)
    future = frame.loc[frame["days_to_event"] >= 0]
    chosen_days = int(future["days_to_event"].min()) if not future.empty else int(frame["days_to_event"].abs().min())
    score = max(window_days - abs(chosen_days), 0) / max(window_days, 1)
    return chosen_days, float(score)


def _underlying_history_from_snapshots(snapshots: Sequence[OptionSnapshot]) -> pd.Series:
    frame = pd.DataFrame(
        {
            "timestamp": [snapshot.timestamp for snapshot in snapshots],
            "underlying_price": [snapshot.underlying_price for snapshot in snapshots],
        }
    ).drop_duplicates(subset=["timestamp"], keep="last")
    frame = frame.sort_values("timestamp")
    if frame.empty:
        return pd.Series(dtype=float)
    return pd.Series(frame["underlying_price"].astype(float).values, index=pd.to_datetime(frame["timestamp"]))


def _positive_squash(value: float, *, scale: float) -> float:
    if not isfinite(value) or value <= 0:
        return 0.0
    return float(np.tanh(value / max(scale, 1e-8)))


def _absolute_squash(value: float, *, scale: float) -> float:
    if not isfinite(value):
        return 0.0
    return float(np.tanh(abs(value) / max(scale, 1e-8)))
