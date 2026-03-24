"""Run the live B3 options scan, compare PETR4 vs PBR IV, and open paper trades."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from math import log
from pathlib import Path
import sys

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
from options_tradebot.utils.polling import repeat_with_interval


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the live B3 options scanner and paper trader.")
    parser.add_argument("--output-dir", default="runtime/live_scan")
    parser.add_argument("--paper-output-dir", default="runtime/live_scan/paper")
    parser.add_argument("--mt5-path")
    parser.add_argument("--mt5-login", type=int)
    parser.add_argument("--mt5-password")
    parser.add_argument("--mt5-server")
    parser.add_argument("--ib-host", default="127.0.0.1")
    parser.add_argument("--ib-port", type=int, default=4002)
    parser.add_argument("--ib-client-id", type=int, default=3)
    parser.add_argument("--b3-dte-min", type=int, default=3)
    parser.add_argument("--b3-dte-max", type=int, default=25)
    parser.add_argument("--max-expiries-per-underlying", type=int, default=2)
    parser.add_argument("--max-strikes-per-right", type=int, default=12)
    parser.add_argument("--moneyness-window", type=float, default=0.20)
    parser.add_argument("--history-bars", type=int, default=90)
    parser.add_argument("--paper-count", type=int, default=3)
    parser.add_argument("--selection-wait-seconds", type=float, default=1.5)
    parser.add_argument("--poll-seconds", type=float, default=300.0)
    parser.add_argument("--run-once", action="store_true")
    return parser


def run_scan(args: argparse.Namespace) -> dict[str, object]:
    settings = default_settings()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paper_output_dir = Path(args.paper_output_dir)
    paper_output_dir.mkdir(parents=True, exist_ok=True)

    mt5_client = MT5MarketDataClient(
        MT5ConnectionConfig(
            path=args.mt5_path or settings.environment.mt5_path,
            login=args.mt5_login or settings.environment.mt5_login,
            password=args.mt5_password or settings.environment.mt5_password,
            server=args.mt5_server or settings.environment.mt5_server,
        )
    )
    b3_snapshots: list[OptionSnapshot] = []
    histories: dict[str, pd.Series] = {}
    underlyings: list[str] = []
    try:
        mt5_client.connect()
        underlyings = mt5_client.available_option_underlyings()
        b3_snapshots = mt5_client.collect_live_option_snapshots(
            underlyings=underlyings,
            dte_min=args.b3_dte_min,
            dte_max=args.b3_dte_max,
            max_expiries_per_underlying=args.max_expiries_per_underlying,
            max_strikes_per_right=args.max_strikes_per_right,
            moneyness_window=args.moneyness_window,
            risk_free_rate=settings.default_risk_free_rate,
            selection_wait_seconds=args.selection_wait_seconds,
        )
        for underlying in underlyings:
            try:
                bars = mt5_client.fetch_bars(symbol=underlying, timeframe="D1", count=args.history_bars)
            except Exception:
                continue
            if "Close" in bars:
                histories[underlying] = bars["Close"].astype(float)
    finally:
        mt5_client.shutdown()

    scanner = MispricingScanner(settings)
    scan_results = scanner.scan_snapshots(
        b3_snapshots,
        top_n=max(len(underlyings), settings.scanner.top_n_underlyings),
        underlying_histories=histories,
    )
    scan_frame = scanner.results_to_frame(scan_results)
    scan_path = output_dir / "b3_underlyings.csv"
    scan_frame.to_csv(scan_path, index=False)

    option_frame = _option_findings_frame(scan_results)
    option_path = output_dir / "b3_top_options.csv"
    option_frame.to_csv(option_path, index=False)

    pbr_snapshots: list[OptionSnapshot] = []
    cross_findings = []
    ib_error: str | None = None
    ib_client = IBGatewayClient(
        IBGatewayConfig(
            host=args.ib_host,
            port=args.ib_port,
            client_id=args.ib_client_id,
            market_data_type=3,
            risk_free_rate=settings.default_usd_risk_free_rate,
        )
    )
    try:
        ib_client.connect()
        pbr_snapshots = ib_client.fetch_option_history_snapshots(
            "PBR",
            max_expiries=2,
            max_strikes=8,
            moneyness_window=0.15,
            duration_str="2 D",
            bar_size="5 mins",
        )
        cross_findings = scanner.find_cross_market_vol_arb_snapshots(b3_snapshots + pbr_snapshots)
    except Exception as error:  # pragma: no cover - integration-driven
        ib_error = str(error)
    finally:
        ib_client.disconnect()

    cross_path = output_dir / "cross_market_vol_arb.csv"
    cross_market_findings_to_frame(cross_findings).to_csv(cross_path, index=False)

    broker = PaperBroker(settings.paper.initial_cash)
    paper_entries = _open_paper_positions(
        broker=broker,
        scan_results=scan_results,
        snapshot_map={snapshot.contract.symbol: snapshot for snapshot in b3_snapshots},
        settings=settings,
        count=args.paper_count,
    )
    paper_state_path = broker.save_state(str(paper_output_dir))
    entries_path = paper_output_dir / "entries.json"
    entries_path.write_text(json.dumps(paper_entries, indent=2, default=str), encoding="utf-8")

    petr4_atm = _atm_snapshot([snapshot for snapshot in b3_snapshots if snapshot.contract.underlying == "PETR4"])
    pbr_atm = _atm_snapshot([snapshot for snapshot in pbr_snapshots if snapshot.contract.underlying == "PBR"])
    petr4_realized = _realized_volatility(histories.get("PETR4"), lookback=settings.scanner.realized_vol_lookback)
    summary = {
        "b3_optionable_underlyings": len(underlyings),
        "b3_snapshots": len(b3_snapshots),
        "scanner_results_path": str(scan_path),
        "top_options_path": str(option_path),
        "cross_market_path": str(cross_path),
        "paper_state_path": str(paper_state_path),
        "paper_entries_path": str(entries_path),
        "ib_error": ib_error,
        "top_underlying": None if not scan_results else asdict(scan_results[0]),
        "top_option": None if option_frame.empty else option_frame.iloc[0].to_dict(),
        "petrobras_cross_market": {
            "petr4_atm_symbol": None if petr4_atm is None else petr4_atm.contract.symbol,
            "petr4_atm_iv": None if petr4_atm is None else petr4_atm.implied_vol,
            "petr4_realized_vol": petr4_realized,
            "petr4_iv_minus_realized": (
                None
                if petr4_atm is None or petr4_atm.implied_vol is None or petr4_realized is None
                else petr4_atm.implied_vol - petr4_realized
            ),
            "pbr_atm_symbol": None if pbr_atm is None else pbr_atm.contract.symbol,
            "pbr_atm_iv": None if pbr_atm is None else pbr_atm.implied_vol,
            "cross_market_top": None if not cross_findings else asdict(cross_findings[0]),
        },
        "paper_entries": paper_entries,
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return summary


def main() -> int:
    args = build_parser().parse_args()
    return repeat_with_interval(
        lambda: run_scan(args),
        interval_seconds=args.poll_seconds,
        run_once=args.run_once,
        task_name="live market scan",
    )


def _option_findings_frame(scan_results) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for result in scan_results:
        for finding in result.top_options:
            row = asdict(finding)
            row["underlying_score"] = result.composite_score
            row["underlying_market"] = result.market
            row["underlying_iv_vs_realized_spread"] = result.iv_vs_realized_spread
            rows.append(row)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["score", "edge_pct"], ascending=[False, False]).reset_index(drop=True)


def _open_paper_positions(
    *,
    broker: PaperBroker,
    scan_results,
    snapshot_map: dict[str, OptionSnapshot],
    settings,
    count: int,
) -> list[dict[str, object]]:
    candidates = []
    for result in scan_results:
        for finding in result.top_options:
            snapshot = snapshot_map.get(finding.symbol)
            if snapshot is None:
                continue
            candidates.append((result, finding, snapshot))
    candidates.sort(
        key=lambda item: (
            item[1].thesis == "SHORT_VOL_RICH_PREMIUM",
            item[1].underlying == "PETR4",
            float(item[1].score),
            abs(float(item[1].edge_pct)),
        ),
        reverse=True,
    )
    opened: list[dict[str, object]] = []
    used_underlyings: set[str] = set()
    for _, finding, snapshot in candidates:
        if len(opened) >= count:
            break
        if snapshot.contract.underlying in used_underlyings:
            continue
        signal = _signal_from_finding(broker=broker, snapshot=snapshot, finding=finding, settings=settings)
        if signal is None:
            continue
        if broker.open_position(signal, snapshot):
            used_underlyings.add(snapshot.contract.underlying)
            opened.append(
                {
                    "symbol": snapshot.contract.symbol,
                    "underlying": snapshot.contract.underlying,
                    "action": signal.action,
                    "contracts": signal.contracts,
                    "entry_price": signal.entry_price,
                    "target_price": signal.target_price,
                    "stop_price": signal.stop_price,
                    "fair_value": signal.fair_value,
                    "fair_volatility": signal.fair_volatility,
                    "reason": signal.reason,
                    "score": signal.score,
                }
            )
    return opened


def _signal_from_finding(
    *,
    broker: PaperBroker,
    snapshot: OptionSnapshot,
    finding,
    settings,
) -> StrategySignal | None:
    is_short = finding.thesis == "SHORT_VOL_RICH_PREMIUM"
    entry_price = snapshot.bid_price if is_short else snapshot.ask_price
    if entry_price <= 0:
        return None
    action_prefix = "SELL" if is_short else "BUY"
    action_suffix = "CALL" if snapshot.contract.option_type == OptionKind.CALL else "PUT"
    base_greeks = snapshot.broker_greeks or black_scholes_greeks(
        spot=snapshot.underlying_price,
        strike=snapshot.contract.strike,
        time_to_expiry=snapshot.time_to_expiry,
        rate=snapshot.risk_free_rate,
        dividend_yield=snapshot.dividend_yield,
        volatility=max(snapshot.implied_vol or finding.fair_volatility, 0.05),
        option_type=snapshot.contract.option_type,
    )
    direction = -1 if is_short else 1
    candidate_greeks = GreekVector(
        delta=base_greeks.delta * snapshot.contract.contract_multiplier * direction,
        gamma=base_greeks.gamma * snapshot.contract.contract_multiplier * direction,
        vega=base_greeks.vega * snapshot.contract.contract_multiplier * direction,
        theta=base_greeks.theta * snapshot.contract.contract_multiplier * direction,
    )
    sizing = size_option_position(
        account_equity=broker.equity(),
        premium=entry_price,
        contract_multiplier=snapshot.contract.contract_multiplier,
        risk_per_trade_pct=settings.risk.risk_per_trade_pct,
        max_contracts=settings.risk.max_contracts,
        greek_limits=GreekLimits(
            max_abs_delta=settings.risk.max_abs_delta,
            max_abs_gamma=settings.risk.max_abs_gamma,
            max_abs_vega=settings.risk.max_abs_vega,
        ),
        current_portfolio_greeks=_aggregate_broker_greeks(broker),
        candidate_greeks=candidate_greeks,
    )
    if sizing.rejected or sizing.contracts <= 0:
        return None
    if is_short:
        target_price = max(entry_price * (1.0 - settings.strategy.take_profit_pct), 0.01)
        stop_price = entry_price * (1.0 + settings.strategy.stop_loss_pct)
        reason = "scanner_short_vol"
    else:
        target_price = max(
            entry_price * (1.0 + settings.strategy.take_profit_pct),
            min(float(finding.fair_value), entry_price * 2.0),
        )
        stop_price = max(entry_price * (1.0 - settings.strategy.stop_loss_pct), 0.01)
        reason = "scanner_long_vol"
    return StrategySignal(
        action=f"{action_prefix}_{action_suffix}",
        contract_symbol=snapshot.contract.symbol,
        underlying=snapshot.contract.underlying,
        contracts=sizing.contracts,
        entry_price=entry_price,
        target_price=target_price,
        stop_price=stop_price,
        fair_value=float(finding.fair_value),
        fair_volatility=float(finding.fair_volatility),
        reason=reason,
        greeks=base_greeks,
        score=float(finding.score),
    )


def _aggregate_broker_greeks(broker: PaperBroker) -> GreekVector | None:
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


def _atm_snapshot(snapshots: list[OptionSnapshot], *, target_dte: int = 14) -> OptionSnapshot | None:
    eligible = [snapshot for snapshot in snapshots if snapshot.implied_vol is not None and snapshot.time_to_expiry > 0]
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


def _realized_volatility(history: pd.Series | None, *, lookback: int) -> float | None:
    if history is None or history.empty:
        return None
    series = pd.Series(history.values, index=pd.to_datetime(history.index), dtype=float).dropna()
    if series.empty:
        return None
    returns = series.map(lambda value: log(max(float(value), 1e-8))).diff().dropna().tail(lookback)
    if returns.empty:
        return None
    return annualized_realized_volatility(returns)


if __name__ == "__main__":
    raise SystemExit(main())
