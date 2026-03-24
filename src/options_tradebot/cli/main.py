"""CLI entry point for the project."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from options_tradebot.config import default_settings
from options_tradebot.data import MT5ConnectionConfig, MT5MarketDataClient, load_snapshot_csv
from options_tradebot.data.models import snapshots_from_frame
from options_tradebot.execution import PaperTradingService
from options_tradebot.research import OptionBacktester, summarize_liquidity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Brazilian options tradebot utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("mt5-probe", help="Probe the MT5 terminal connection.")
    probe.add_argument("--mt5-path")
    probe.add_argument("--mt5-login", type=int)
    probe.add_argument("--mt5-password")
    probe.add_argument("--mt5-server")

    research = subparsers.add_parser("research-summary", help="Summarize an options snapshot CSV.")
    research.add_argument("--snapshots", required=True)

    backtest = subparsers.add_parser("backtest", help="Run a backtest over a snapshot CSV.")
    backtest.add_argument("--snapshots", required=True)
    backtest.add_argument("--output-dir")

    paper = subparsers.add_parser("paper", help="Run a single paper-trading step.")
    paper.add_argument("--snapshots", required=True)
    paper.add_argument("--output-dir")

    return parser


def main() -> int:
    settings = default_settings()
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "mt5-probe":
        client = MT5MarketDataClient(
            MT5ConnectionConfig(
                path=args.mt5_path or settings.environment.mt5_path,
                login=args.mt5_login or settings.environment.mt5_login,
                password=args.mt5_password or settings.environment.mt5_password,
                server=args.mt5_server or settings.environment.mt5_server,
            )
        )
        try:
            payload = client.connect()
            print(json.dumps(payload, indent=2, default=str))
            return 0
        finally:
            client.shutdown()

    if args.command == "research-summary":
        frame = load_snapshot_csv(args.snapshots)
        summary = summarize_liquidity(frame)
        print(summary.to_string(index=False))
        return 0

    if args.command == "backtest":
        frame = load_snapshot_csv(args.snapshots)
        output_dir = args.output_dir or "runtime/backtest"
        result = OptionBacktester(settings).run(frame, output_dir=output_dir)
        print(json.dumps(result.summary(), indent=2))
        print(Path(output_dir).resolve())
        return 0

    if args.command == "paper":
        frame = load_snapshot_csv(args.snapshots)
        snapshots = snapshots_from_frame(frame)
        output_dir = args.output_dir or settings.paper.output_dir
        service = PaperTradingService(settings=settings, output_dir=output_dir)
        result = service.run_once(snapshots)
        print(json.dumps({"equity": result.equity, "signal": result.signal.action}, indent=2))
        print(result.state_path)
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 1
