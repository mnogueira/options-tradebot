"""Cross-sectional mispricing scanner for B3 options."""

from __future__ import annotations

from dataclasses import asdict
from math import exp
from pathlib import Path

import numpy as np
import pandas as pd

from options_tradebot.config.settings import AppSettings, default_settings
from options_tradebot.data.models import snapshots_from_frame
from options_tradebot.market.models import OptionKind, OptionSnapshot, UnderlyingType
from options_tradebot.market.pricing import (
    annualized_realized_volatility,
    black_76_price,
    black_scholes_price,
    corrado_su_price,
)
from options_tradebot.market.surface import calibrate_surface
from options_tradebot.scanner.models import (
    OptionMispricingFinding,
    UnderlyingScanResult,
    scan_results_to_frame,
)
from options_tradebot.scanner.sources import discover_optionable_assets


class MispricingScanner:
    """Rank B3 underlyings by current options mispricing likelihood."""

    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or default_settings()

    def scan_snapshots(
        self,
        snapshots: list[OptionSnapshot],
        *,
        events: pd.DataFrame | None = None,
        top_n: int | None = None,
    ) -> list[UnderlyingScanResult]:
        """Scan an in-memory snapshot list."""

        frame = pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp(snapshot.timestamp),
                    "symbol": snapshot.contract.symbol,
                    "underlying": snapshot.contract.underlying,
                    "option_type": snapshot.contract.option_type.value,
                    "strike": snapshot.contract.strike,
                    "expiry": pd.Timestamp(snapshot.contract.expiry),
                    "underlying_type": snapshot.contract.underlying_type.value,
                    "contract_multiplier": snapshot.contract.contract_multiplier,
                    "bid": snapshot.quote.bid,
                    "ask": snapshot.quote.ask,
                    "last": snapshot.quote.last,
                    "volume": snapshot.quote.volume,
                    "open_interest": snapshot.quote.open_interest,
                    "underlying_price": snapshot.underlying_price,
                    "risk_free_rate": snapshot.risk_free_rate,
                    "dividend_yield": snapshot.dividend_yield,
                    "implied_vol": snapshot.implied_vol,
                    "underlying_forward": snapshot.underlying_forward,
                }
                for snapshot in snapshots
            ]
        )
        return self.scan_frame(frame, events=events, top_n=top_n)

    def scan_frame(
        self,
        frame: pd.DataFrame,
        *,
        events: pd.DataFrame | None = None,
        top_n: int | None = None,
    ) -> list[UnderlyingScanResult]:
        """Scan a normalized option snapshot frame."""

        if frame.empty:
            return []
        working = frame.copy()
        working["timestamp"] = pd.to_datetime(working["timestamp"])
        working["expiry"] = pd.to_datetime(working["expiry"])
        universe = discover_optionable_assets(snapshot_frame=working)
        raw_results: list[dict[str, object]] = []

        for underlying in universe:
            underlying_frame = working.loc[working["underlying"] == underlying].copy()
            if underlying_frame.empty:
                continue
            latest_timestamp = underlying_frame["timestamp"].max()
            latest_slice = underlying_frame.loc[underlying_frame["timestamp"] == latest_timestamp].copy()
            latest_slice = latest_slice.dropna(subset=["bid", "ask", "underlying_price"])
            if latest_slice.empty:
                continue

            iv_series = self._atm_iv_series(underlying_frame)
            current_atm_iv = None if iv_series.empty else float(iv_series.iloc[-1])
            underlying_history = self._underlying_history(underlying_frame)
            realized_vol = self._realized_vol(underlying_history)
            iv_rank, iv_percentile = self._iv_rank_metrics(iv_series)
            skew_series = self._skew_series(underlying_frame)
            current_skew = 0.0 if skew_series.empty else float(skew_series.iloc[-1])
            skew_anomaly = self._zscore_from_history(skew_series)
            flow_series = self._flow_ratio_series(underlying_frame)
            current_flow = 0.0 if flow_series.empty else float(flow_series.iloc[-1])
            flow_spike = self._spike_ratio(flow_series)
            median_spread_pct = self._median_spread_pct(latest_slice)
            bid_ask_quality = max(
                1.0 - median_spread_pct / max(self.settings.scanner.max_tradeable_spread_pct, 1e-6),
                0.0,
            )
            next_event_days, event_proximity = self._event_metrics(underlying, latest_timestamp, events)

            chain = snapshots_from_frame(latest_slice)
            surface, diagnostics = calibrate_surface(
                chain,
                method=self.settings.scanner.surface_method,
                min_points=self.settings.scanner.min_surface_points,
            )
            option_findings = self._rank_option_findings(chain, surface, underlying_history)
            best_option_edge_pct = option_findings[0].edge_pct if option_findings else 0.0
            skew_alpha_score = (
                max(skew_anomaly, 0.0)
                if underlying in self.settings.scanner.skew_alpha_underlyings
                else 0.0
            )

            raw_results.append(
                {
                    "underlying": underlying,
                    "latest_timestamp": latest_timestamp.isoformat(),
                    "realized_volatility": realized_vol,
                    "atm_implied_volatility": current_atm_iv,
                    "iv_vs_realized_spread": (
                        0.0 if current_atm_iv is None else current_atm_iv - realized_vol
                    ),
                    "iv_rank": iv_rank,
                    "iv_percentile": iv_percentile,
                    "put_call_skew": current_skew,
                    "skew_anomaly": skew_anomaly,
                    "skew_alpha_score": skew_alpha_score,
                    "volume_open_interest_ratio": current_flow,
                    "volume_open_interest_spike": flow_spike,
                    "median_spread_pct": median_spread_pct,
                    "bid_ask_quality": bid_ask_quality,
                    "next_event_days": next_event_days,
                    "event_proximity_score": event_proximity,
                    "best_option_edge_pct": best_option_edge_pct,
                    "surface_method": diagnostics.model_name,
                    "top_options": tuple(option_findings[: self.settings.scanner.top_option_count]),
                }
            )

        if not raw_results:
            return []

        scored = self._apply_cross_sectional_scores(raw_results)
        top_count = top_n or self.settings.scanner.top_n_underlyings
        ordered = sorted(scored, key=lambda item: item["composite_score"], reverse=True)
        return [UnderlyingScanResult(**item) for item in ordered[:top_count]]

    @staticmethod
    def results_to_frame(results: list[UnderlyingScanResult]) -> pd.DataFrame:
        """Flatten scanner results into a DataFrame."""

        return scan_results_to_frame(results)

    @staticmethod
    def save_results(results: list[UnderlyingScanResult], output: str) -> Path:
        """Persist scanner results to CSV and JSON sidecars."""

        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        frame = scan_results_to_frame(results)
        frame.to_csv(output_path, index=False)
        json_path = output_path.with_suffix(".json")
        json_path.write_text(
            pd.Series([asdict(result) for result in results]).to_json(orient="values", indent=2),
            encoding="utf-8",
        )
        return output_path

    def _rank_option_findings(
        self,
        chain: list[OptionSnapshot],
        surface,
        underlying_history: pd.Series,
    ) -> list[OptionMispricingFinding]:
        if not chain:
            return []
        returns = np.log(underlying_history.astype(float)).diff().dropna()
        skewness = float(returns.skew()) if returns.size >= 3 else 0.0
        kurtosis = float(returns.kurt()) + 3.0 if returns.size >= 4 else 3.0
        findings: list[OptionMispricingFinding] = []

        for snapshot in chain:
            if snapshot.ask_price <= 0 or snapshot.mid_price <= 0:
                continue
            fair_vol = surface.volatility_for_snapshot(snapshot)
            fair_value = self._fair_value(snapshot, fair_vol, skewness, kurtosis)
            market_price = snapshot.mid_price
            edge_reais = market_price - fair_value
            edge_pct = edge_reais / max(market_price, 1e-8)
            spread_quality = max(
                1.0 - snapshot.quote.spread_pct / max(self.settings.scanner.max_tradeable_spread_pct, 1e-6),
                0.05,
            )
            score = edge_pct * spread_quality * np.log1p(snapshot.quote.volume + (snapshot.quote.open_interest or 0))
            findings.append(
                OptionMispricingFinding(
                    symbol=snapshot.contract.symbol,
                    underlying=snapshot.contract.underlying,
                    option_type=snapshot.contract.option_type,
                    strike=snapshot.contract.strike,
                    expiry=snapshot.contract.expiry.isoformat(),
                    implied_vol=snapshot.implied_vol,
                    fair_volatility=fair_vol,
                    market_price=market_price,
                    fair_value=fair_value,
                    edge_reais=edge_reais,
                    edge_pct=edge_pct,
                    spread_pct=snapshot.quote.spread_pct,
                    volume=snapshot.quote.volume,
                    open_interest=snapshot.quote.open_interest,
                    thesis="SHORT_VOL_RICH_PREMIUM" if edge_reais >= 0 else "LONG_VOL_CHEAP_PREMIUM",
                    score=float(score),
                )
            )

        findings.sort(key=lambda item: (item.score, abs(item.edge_pct)), reverse=True)
        return findings

    def _fair_value(
        self,
        snapshot: OptionSnapshot,
        fair_vol: float,
        skewness: float,
        kurtosis: float,
    ) -> float:
        if snapshot.contract.underlying_type == UnderlyingType.FUTURE:
            return black_76_price(
                forward=snapshot.forward_price,
                strike=snapshot.contract.strike,
                time_to_expiry=snapshot.time_to_expiry,
                rate=snapshot.risk_free_rate,
                volatility=fair_vol,
                option_type=snapshot.contract.option_type,
            )
        if self.settings.scanner.use_corrado_su_adjustment:
            return corrado_su_price(
                spot=snapshot.underlying_price,
                strike=snapshot.contract.strike,
                time_to_expiry=snapshot.time_to_expiry,
                rate=snapshot.risk_free_rate,
                dividend_yield=snapshot.dividend_yield,
                volatility=fair_vol,
                skewness=skewness,
                kurtosis=kurtosis,
                option_type=snapshot.contract.option_type,
            )
        return black_scholes_price(
            spot=snapshot.underlying_price,
            strike=snapshot.contract.strike,
            time_to_expiry=snapshot.time_to_expiry,
            rate=snapshot.risk_free_rate,
            dividend_yield=snapshot.dividend_yield,
            volatility=fair_vol,
            option_type=snapshot.contract.option_type,
        )

    def _apply_cross_sectional_scores(self, rows: list[dict[str, object]]) -> list[dict[str, object]]:
        metrics = {
            "iv_vs_realized_spread": self._robust_zscores(
                [float(row["iv_vs_realized_spread"]) for row in rows]
            ),
            "iv_rank": self._robust_zscores([float(row["iv_rank"]) for row in rows]),
            "skew_anomaly": self._robust_zscores([abs(float(row["skew_anomaly"])) for row in rows]),
            "volume_open_interest_spike": self._robust_zscores(
                [float(row["volume_open_interest_spike"]) for row in rows]
            ),
            "bid_ask_quality": self._robust_zscores([float(row["bid_ask_quality"]) for row in rows]),
            "event_proximity_score": self._robust_zscores(
                [float(row["event_proximity_score"]) for row in rows]
            ),
            "skew_alpha_score": self._robust_zscores(
                [float(row["skew_alpha_score"]) for row in rows]
            ),
            "best_option_edge_pct": self._robust_zscores(
                [float(row["best_option_edge_pct"]) for row in rows]
            ),
        }
        scored: list[dict[str, object]] = []
        for index, row in enumerate(rows):
            composite = (
                self.settings.scanner.weight_iv_spread * metrics["iv_vs_realized_spread"][index]
                + self.settings.scanner.weight_iv_rank * metrics["iv_rank"][index]
                + self.settings.scanner.weight_skew * metrics["skew_anomaly"][index]
                + self.settings.scanner.weight_flow * metrics["volume_open_interest_spike"][index]
                + self.settings.scanner.weight_liquidity * metrics["bid_ask_quality"][index]
                + self.settings.scanner.weight_event * metrics["event_proximity_score"][index]
                + self.settings.scanner.weight_skew_alpha * metrics["skew_alpha_score"][index]
                + self.settings.scanner.weight_option_edge * metrics["best_option_edge_pct"][index]
            )
            scored.append({**row, "composite_score": float(composite)})
        return scored

    @staticmethod
    def _robust_zscores(values: list[float]) -> list[float]:
        array = np.asarray(values, dtype=float)
        if array.size == 0:
            return []
        median = float(np.median(array))
        mad = float(np.median(np.abs(array - median)))
        scale = 1.4826 * mad
        if scale <= 1e-9:
            std = float(np.std(array, ddof=1)) if array.size > 1 else 0.0
            if std <= 1e-9:
                return [0.0 for _ in values]
            return [float((value - float(np.mean(array))) / std) for value in array]
        return [float((value - median) / scale) for value in array]

    @staticmethod
    def _underlying_history(frame: pd.DataFrame) -> pd.Series:
        history = (
            frame.loc[:, ["timestamp", "underlying_price"]]
            .drop_duplicates(subset=["timestamp"], keep="last")
            .sort_values("timestamp")
        )
        return pd.Series(history["underlying_price"].astype(float).values, index=history["timestamp"])

    def _realized_vol(self, history: pd.Series) -> float:
        returns = np.log(history.astype(float)).diff().dropna().tail(
            self.settings.scanner.realized_vol_lookback
        )
        return annualized_realized_volatility(returns)

    def _atm_iv_series(self, frame: pd.DataFrame) -> pd.Series:
        values: list[tuple[pd.Timestamp, float]] = []
        for timestamp, slice_frame in frame.groupby("timestamp"):
            live = slice_frame.dropna(subset=["implied_vol"]).copy()
            if live.empty:
                continue
            spot = float(live["underlying_price"].iloc[-1])
            dte = (live["expiry"] - pd.Timestamp(timestamp)).dt.days.abs()
            score = np.abs(np.log(live["strike"].astype(float) / max(spot, 1e-8))) + (
                np.abs(dte - self.settings.scanner.atm_target_dte) / 365.0
            )
            live = live.assign(_atm_score=score).sort_values("_atm_score")
            values.append((pd.Timestamp(timestamp), float(live["implied_vol"].head(4).median())))
        if not values:
            return pd.Series(dtype=float)
        return pd.Series([item[1] for item in values], index=[item[0] for item in values]).sort_index()

    def _skew_series(self, frame: pd.DataFrame) -> pd.Series:
        values: list[tuple[pd.Timestamp, float]] = []
        for timestamp, slice_frame in frame.groupby("timestamp"):
            skew = self._slice_skew(slice_frame, pd.Timestamp(timestamp))
            if skew is not None:
                values.append((pd.Timestamp(timestamp), skew))
        if not values:
            return pd.Series(dtype=float)
        return pd.Series([item[1] for item in values], index=[item[0] for item in values]).sort_index()

    def _slice_skew(self, frame: pd.DataFrame, timestamp: pd.Timestamp) -> float | None:
        live = frame.dropna(subset=["implied_vol"]).copy()
        if live.empty:
            return None
        live["dte_gap"] = (live["expiry"] - timestamp).dt.days.sub(self.settings.scanner.atm_target_dte).abs()
        target_expiry = live.sort_values("dte_gap")["expiry"].iloc[0]
        expiry_slice = live.loc[live["expiry"] == target_expiry].copy()
        spot = float(expiry_slice["underlying_price"].iloc[-1])
        puts = expiry_slice.loc[
            (expiry_slice["option_type"].astype(str).str.lower() == OptionKind.PUT.value)
            & (expiry_slice["strike"].astype(float) <= spot * 0.99)
        ]
        calls = expiry_slice.loc[
            (expiry_slice["option_type"].astype(str).str.lower() == OptionKind.CALL.value)
            & (expiry_slice["strike"].astype(float) >= spot * 1.01)
        ]
        if puts.empty or calls.empty:
            puts = expiry_slice.loc[expiry_slice["option_type"].astype(str).str.lower() == OptionKind.PUT.value]
            calls = expiry_slice.loc[expiry_slice["option_type"].astype(str).str.lower() == OptionKind.CALL.value]
        if puts.empty or calls.empty:
            return None
        return float(puts["implied_vol"].median() - calls["implied_vol"].median())

    @staticmethod
    def _flow_ratio_series(frame: pd.DataFrame) -> pd.Series:
        flow = (
            frame.assign(open_interest=lambda value: value["open_interest"].fillna(0))
            .groupby("timestamp", dropna=False)
            .agg(total_volume=("volume", "sum"), total_open_interest=("open_interest", "sum"))
        )
        denominator = flow["total_open_interest"].replace(0, pd.NA)
        series = (flow["total_volume"] / denominator).fillna(0.0)
        return series.sort_index()

    @staticmethod
    def _spike_ratio(series: pd.Series) -> float:
        if series.empty:
            return 0.0
        current = float(series.iloc[-1])
        baseline = float(series.iloc[:-1].tail(20).mean()) if series.size > 1 else 0.0
        if baseline <= 1e-9:
            return current
        return current / baseline

    @staticmethod
    def _median_spread_pct(frame: pd.DataFrame) -> float:
        spread = frame["ask"].astype(float) - frame["bid"].astype(float)
        mid = ((frame["ask"].astype(float) + frame["bid"].astype(float)) / 2.0).replace(0, pd.NA)
        return float((spread / mid).fillna(1.0).median())

    def _event_metrics(
        self,
        underlying: str,
        latest_timestamp: pd.Timestamp,
        events: pd.DataFrame | None,
    ) -> tuple[int | None, float]:
        if events is None or events.empty:
            return None, 0.0
        working = events.copy()
        working["event_date"] = pd.to_datetime(working["event_date"])
        ticker_column = "ticker" if "ticker" in working.columns else "underlying"
        subset = working.loc[working[ticker_column].astype(str).str.upper() == underlying.upper()]
        if subset.empty:
            return None, 0.0
        days = (
            subset["event_date"].dt.normalize() - latest_timestamp.normalize()
        ).dt.days
        upcoming = days.loc[days >= 0]
        if upcoming.empty:
            return None, 0.0
        next_event_days = int(upcoming.min())
        if next_event_days > self.settings.scanner.event_window_days:
            return next_event_days, 0.0
        score = exp(-next_event_days / max(self.settings.scanner.event_decay_days, 1e-6))
        return next_event_days, float(score)

    @staticmethod
    def _iv_rank_metrics(series: pd.Series) -> tuple[float, float]:
        if series.empty:
            return 0.0, 0.0
        current = float(series.iloc[-1])
        minimum = float(series.min())
        maximum = float(series.max())
        if maximum - minimum <= 1e-9:
            rank = 0.5
        else:
            rank = (current - minimum) / (maximum - minimum)
        percentile = float((series <= current).mean())
        return float(rank), percentile

    @staticmethod
    def _zscore_from_history(series: pd.Series) -> float:
        if series.empty:
            return 0.0
        if series.size == 1:
            return float(series.iloc[-1])
        baseline = series.iloc[:-1]
        if baseline.empty:
            return float(series.iloc[-1])
        mean = float(baseline.mean())
        std = float(baseline.std(ddof=1))
        if std <= 1e-9:
            return float(series.iloc[-1] - mean)
        return float((series.iloc[-1] - mean) / std)
