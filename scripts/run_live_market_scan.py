"""Run a live B3 options scan with MT5 plus a PBR cross-market overlay from IB."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from math import log
from pathlib import Path
import sys

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from options_tradebot.config import default_settings
from options_tradebot.connectors.ib import IBGatewayClient, IBGatewayConfig
from options_tradebot.data import MT5ConnectionConfig, MT5MarketDataClient
from options_tradebot.execution import PaperBroker
from options_tradebot.market import (
    GreekVector,
    OptionKind,
    OptionSnapshot,
    annualized_realized_volatility,
    black_scholes_greeks,
)
from options_tradebot.risk.sizing import GreekLimits, size_option_position
from options_tradebot.scanner import MispricingScanner, cross_market_findings_to_frame
from options_tradebot.strategies import StrategySignal


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the live MT5/IB options scanner and seed paper trades.")
    parser.add_argument("--output-dir", default="runtime/live_scan")
    parser.add_argument("--paper-output-dir", default="runtime/paper_live_scanner")
    parser.add_argument("--mt5-path")
    parser.add_argument("--mt5-login", type=int)
    parser.add_argument("--mt5-password")
    parser.add_argument("--mt5-server")
    parser.add_argument("--ib-host", default="127.0.0.1")
    parser.add_argument("--ib-port", type=int, default=4002)
    parser.add_argument("--ib-client-id", type=int, default=21)
    parser.add_argument("--dte-min", type=int, default=3)
    parser.add_argument("--dte-max", type=int, default=25)
    parser.add_argument("--max-expiries-per-underlying", type=int, default=2)
    parser.add_argument("--max-strikes-per-right", type=int, default=12)
    parser.add_argument("--moneyness-window", type=float, default=0.20)
    parser.add_argument("--history-bars", type=int, default=90)
    parser.add_argument("--ib-max-expiries", type=int, default=2)
    parser.add_argument("--ib-max-strikes", type=int, default=12)
    parser.add_argument("--ib-moneyness-window", type=float, default=0.20)
    parser.add_argument("--ib-bar-size", default="5 mins")
    parser.add_argument("--ib-duration", default="2 D")
    parser.add_argument("--paper-top", type=int, default=3)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = default_settings()
    output_dir = Path(args.output_dir)
    paper_output_dir = Path(args.paper_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paper_output_dir.mkdir(parents=True, exist_ok=True)

    mt5_client = MT5MarketDataClient(
        MT5ConnectionConfig(
            path=args.mt5_path or settings.environment.mt5_path,
            login=args.mt5_login or settings.environment.mt5_login,
            password=args.mt5_password or settings.environment.mt5_password,
            server=args.mt5_server or settings.environment.mt5_server,
        )
    )
    mt5_payload: dict[str, object] | None = None
    underlyings: list[str] = []
    histories: dict[str, pd.Series] = {}
    b3_snapshots: list[OptionSnapshot] = []
    try:
        mt5_payload = mt5_client.connect()
        underlyings = mt5_client.available_option_underlyings()
        histories = _collect_underlying_histories(mt5_client, underlyings, count=args.history_bars)
        b3_snapshots = mt5_client.collect_live_option_snapshots(
            underlyings=underlyings,
            dte_min=args.dte_min,
            dte_max=args.dte_max,
            max_expiries_per_underlying=args.max_expiries_per_underlying,
            max_strikes_per_right=args.max_strikes_per_right,
            moneyness_window=args.moneyness_window,
            risk_free_rate=settings.default_risk_free_rate,
            dividend_yield=0.0,
        )
    finally:
        mt5_client.shutdown()

    if not b3_snapshots:
        raise RuntimeError("No live B3 option snapshots were collected from MT5.")

    scanner = MispricingScanner(settings)
    scanned_underlying_count = len({snapshot.contract.underlying for snapshot in b3_snapshots})
    b3_results = scanner.scan_snapshots(
        b3_snapshots,
        top_n=scanned_underlying_count,
        underlying_histories=histories,
    )
    scanner.save_results(b3_results, str(output_dir / "b3_underlyings.csv"))
    top_options_frame = _option_findings_frame(b3_results)
    top_options_frame.to_csv(output_dir / "b3_top_options.csv", index=False)

    petr_history = histories.get("PETR4")
    petr_realized = _realized_vol_from_history(petr_history, lookback=settings.scanner.realized_vol_lookback)
    petr_atm = _select_atm_snapshot(
        [snapshot for snapshot in b3_snapshots if snapshot.contract.underlying == "PETR4"],
        target_dte=settings.scanner.atm_target_dte,
    )

    ib_client = IBGatewayClient(
        IBGatewayConfig(
            host=args.ib_host,
            port=args.ib_port,
            client_id=args.ib_client_id,
            market_data_type=settings.environment.ib_market_data_type,
            risk_free_rate=settings.default_usd_risk_free_rate,
        )
    )
    ib_payload: dict[str, object] | None = None
    pbr_snapshots: list[OptionSnapshot] = []
    pbr_history: pd.Series | None = None
    try:
        ib_payload = ib_client.connect()
        pbr_history = _fetch_ib_underlying_history(ib_client, "PBR", count=60)
        pbr_snapshots = ib_client.fetch_option_history_snapshots(
            "PBR",
            max_expiries=args.ib_max_expiries,
            max_strikes=args.ib_max_strikes,
            moneyness_window=args.ib_moneyness_window,
            duration_str=args.ib_duration,
            bar_size=args.ib_bar_size,
        )
    finally:
        ib_client.disconnect()

    combined_snapshots = [*b3_snapshots, *pbr_snapshots]
    cross_market_findings = scanner.find_cross_market_vol_arb_snapshots(combined_snapshots)
    cross_market_frame = cross_market_findings_to_frame(cross_market_findings)
    cross_market_frame.to_csv(output_dir / "cross_market_vol_arb.csv", index=False)

    pbr_atm = _select_atm_snapshot(
        [snapshot for snapshot in pbr_snapshots if snapshot.contract.underlying == "PBR"],
        target_dte=settings.scanner.atm_target_dte,
    )
    pbr_realized = _realized_vol_from_history(pbr_history, lookback=settings.scanner.realized_vol_lookback)

    broker = PaperBroker(settings.paper.initial_cash)
    snapshot_map = {snapshot.contract.symbol: snapshot for snapshot in b3_snapshots}
    opened_signals = _open_short_vol_positions(
        broker=broker,
        results=b3_results,
        snapshot_map=snapshot_map,
        settings=settings,
        limit=args.paper_top,
    )
    paper_state_path = broker.save_state(str(paper_output_dir))
    (paper_output_dir / "opened_signals.json").write_text(
        json.dumps([_signal_payload(item) for item in opened_signals], indent=2, default=str),
        encoding="utf-8",
    )

    summary = {
        "mt5_connection": mt5_payload,
        "ib_connection": ib_payload,
        "b3_underlyings_total": len(underlyings),
        "b3_underlyings_scanned": scanned_underlying_count,
        "b3_option_snapshots": len(b3_snapshots),
        "ib_pbr_option_snapshots": len(pbr_snapshots),
        "petr4_atm_symbol": None if petr_atm is None else petr_atm.contract.symbol,
        "petr4_atm_iv": None if petr_atm is None else petr_atm.implied_vol,
        "petr4_realized_vol_20d": petr_realized,
        "petr4_iv_minus_realized": (
            None if petr_atm is None or petr_atm.implied_vol is None else petr_atm.implied_vol - petr_realized
        ),
        "pbr_atm_symbol": None if pbr_atm is None else pbr_atm.contract.symbol,
        "pbr_atm_iv": None if pbr_atm is None else pbr_atm.implied_vol,
        "pbr_realized_vol_20d": pbr_realized,
        "cross_market_top_action": None if not cross_market_findings else cross_market_findings[0].action,
        "cross_market_top_iv_gap": None if not cross_market_findings else cross_market_findings[0].iv_gap,
        "top_b3_options": top_options_frame.head(10).to_dict(orient="records"),
        "paper_positions_opened": len(opened_signals),
        "paper_state_path": str(paper_state_path),
    }
    (output_dir / "scan_summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _collect_underlying_histories(
    client: MT5MarketDataClient,
    underlyings: list[str],
    *,
    count: int,
) -> dict[str, pd.Series]:
    histories: dict[str, pd.Series] = {}
    for underlying in underlyings:
        try:
            bars = client.fetch_bars(symbol=underlying, timeframe="D1", count=count)
        except Exception:
            continue
        closes = bars["Close"].astype(float).dropna()
        if not closes.empty:
            histories[underlying] = closes
    return histories


def _fetch_ib_underlying_history(
    client: IBGatewayClient,
    symbol: str,
    *,
    count: int,
) -> pd.Series | None:
    contract = client.qualify_equity(symbol)
    bars = client._require_connected().reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=f"{max(count, 5)} D",
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=2,
        keepUpToDate=False,
        chartOptions=[],
        timeout=max(client.config.timeout, 1.0) * 10.0,
    )
    if not bars:
        return None
    frame = pd.DataFrame(
        {
            "date": [getattr(bar, "date", None) for bar in bars],
            "close": [getattr(bar, "close", None) for bar in bars],
        }
    ).dropna(subset=["date", "close"])
    if frame.empty:
        return None
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    frame = frame.tail(count)
    return pd.Series(frame["close"].astype(float).values, index=frame["date"])


def _realized_vol_from_history(history: pd.Series | None, *, lookback: int) -> float:
    if history is None or history.empty:
        return 0.0
    returns = np.log(history.astype(float)).diff().dropna().tail(lookback)
    return annualized_realized_volatility(returns)


def _select_atm_snapshot(chain: list[OptionSnapshot], *, target_dte: int) -> OptionSnapshot | None:
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


def _option_findings_frame(results) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for rank, result in enumerate(results, start=1):
        for option in result.top_options:
            row = asdict(option)
            row["underlying_rank"] = rank
            row["underlying_score"] = result.composite_score
            row["realized_volatility"] = result.realized_volatility
            row["atm_implied_volatility"] = result.atm_implied_volatility
            row["iv_vs_realized_spread"] = result.iv_vs_realized_spread
            rows.append(row)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["score", "edge_pct"], ascending=[False, False]).reset_index(drop=True)


def _open_short_vol_positions(
    *,
    broker: PaperBroker,
    results,
    snapshot_map: dict[str, OptionSnapshot],
    settings,
    limit: int,
) -> list[dict[str, object]]:
    opened: list[dict[str, object]] = []
    seen_underlyings: set[str] = set()
    for result in results:
        for finding in result.top_options:
            if len(opened) >= limit:
                return opened
            if finding.market != "B3" or finding.edge_pct <= 0 or finding.thesis != "SHORT_VOL_RICH_PREMIUM":
                continue
            if finding.underlying in seen_underlyings:
                continue
            snapshot = snapshot_map.get(finding.symbol)
            if snapshot is None or snapshot.bid_price <= 0:
                continue
            greeks = snapshot.broker_greeks or black_scholes_greeks(
                spot=snapshot.underlying_price,
                strike=snapshot.contract.strike,
                time_to_expiry=snapshot.time_to_expiry,
                rate=snapshot.risk_free_rate,
                dividend_yield=snapshot.dividend_yield,
                volatility=max(snapshot.implied_vol or finding.fair_volatility, 0.05),
                option_type=snapshot.contract.option_type,
            )
            sizing = size_option_position(
                account_equity=broker.equity(),
                premium=snapshot.bid_price,
                contract_multiplier=snapshot.contract.contract_multiplier,
                risk_per_trade_pct=settings.risk.risk_per_trade_pct,
                max_contracts=settings.risk.max_contracts,
                greek_limits=GreekLimits(
                    max_abs_delta=settings.risk.max_abs_delta,
                    max_abs_gamma=settings.risk.max_abs_gamma,
                    max_abs_vega=settings.risk.max_abs_vega,
                ),
                current_portfolio_greeks=_aggregate_greeks(broker),
                candidate_greeks=GreekVector(
                    delta=-greeks.delta * snapshot.contract.contract_multiplier,
                    gamma=-greeks.gamma * snapshot.contract.contract_multiplier,
                    vega=-greeks.vega * snapshot.contract.contract_multiplier,
                    theta=-greeks.theta * snapshot.contract.contract_multiplier,
                ),
            )
            if sizing.rejected or sizing.contracts <= 0:
                continue
            entry_price = snapshot.bid_price
            signal = StrategySignal(
                action="SELL_CALL" if snapshot.contract.option_type == OptionKind.CALL else "SELL_PUT",
                contract_symbol=snapshot.contract.symbol,
                underlying=snapshot.contract.underlying,
                contracts=sizing.contracts,
                entry_price=entry_price,
                target_price=max(entry_price * (1.0 - settings.strategy.take_profit_pct), 0.01),
                stop_price=entry_price * (1.0 + settings.strategy.stop_loss_pct),
                fair_value=finding.fair_value,
                fair_volatility=finding.fair_volatility,
                reason="scanner_short_vol",
                greeks=greeks,
                score=finding.score,
            )
            if broker.open_position(signal, snapshot):
                opened.append(
                    {
                        "signal": signal,
                        "finding": finding,
                        "entry_price": entry_price,
                        "contracts": sizing.contracts,
                    }
                )
                seen_underlyings.add(finding.underlying)
    return opened


def _aggregate_greeks(broker: PaperBroker) -> GreekVector | None:
    if not broker.positions:
        return None
    delta = 0.0
    gamma = 0.0
    vega = 0.0
    theta = 0.0
    for position in broker.positions.values():
        delta += position.current_greeks.delta
        gamma += position.current_greeks.gamma
        vega += position.current_greeks.vega
        theta += position.current_greeks.theta
    return GreekVector(delta=delta, gamma=gamma, vega=vega, theta=theta)


def _signal_payload(item: dict[str, object]) -> dict[str, object]:
    signal = item["signal"]
    finding = item["finding"]
    return {
        "signal": asdict(signal),
        "finding": asdict(finding),
        "entry_price": item["entry_price"],
        "contracts": item["contracts"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
