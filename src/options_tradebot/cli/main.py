"""CLI entry point for the defined-risk short-vol runtime."""

from __future__ import annotations

import argparse
import json

from options_tradebot.config import load_short_vol_config
from options_tradebot.connectors.ib import IBGatewayClient, IBGatewayConfig
from options_tradebot.data import MT5ConnectionConfig, MT5MarketDataClient
from options_tradebot.runtime import DefinedRiskShortVolRuntime, bootstrap_runtime_config
from options_tradebot.utils.polling import repeat_with_interval


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Defined-risk short-vol runtime utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    mt5_probe = subparsers.add_parser("mt5-probe", help="Probe one MT5 endpoint from the canonical config.")
    mt5_probe.add_argument("--config", default="config/defined_risk_short_vol.toml")
    mt5_probe.add_argument("--target", choices=["data", "paper", "live"], default="data")

    ib_probe = subparsers.add_parser("ib-probe", help="Probe one IB endpoint from the canonical config.")
    ib_probe.add_argument("--config", default="config/defined_risk_short_vol.toml")
    ib_probe.add_argument("--target", choices=["data", "paper", "live"], default="data")

    run_short_vol = subparsers.add_parser("run-short-vol", help="Run the unified defined-risk short-vol runtime.")
    run_short_vol.add_argument("--config", default="config/defined_risk_short_vol.toml")
    run_short_vol.add_argument("--mode", choices=["sim", "paper-broker", "live"])
    run_short_vol.add_argument("--venues")
    run_short_vol.add_argument("--run-once", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "mt5-probe":
        config = load_short_vol_config(args.config)
        endpoint = getattr(config.venues.mt5, args.target)
        client = MT5MarketDataClient(
            MT5ConnectionConfig(
                path=endpoint.path,
                login=endpoint.login,
                password=endpoint.password,
                server=endpoint.server,
            )
        )
        try:
            payload = client.connect()
            print(json.dumps(payload, indent=2, default=str))
            return 0
        finally:
            client.shutdown()

    if args.command == "ib-probe":
        config = load_short_vol_config(args.config)
        data_endpoint = config.venues.ib.data
        execution_target = config.venues.ib.paper if args.target == "paper" else config.venues.ib.live
        client = IBGatewayClient(
            IBGatewayConfig(
                host=data_endpoint.host,
                data_port=data_endpoint.port,
                data_client_id=data_endpoint.client_id,
                execution_port=execution_target.port,
                execution_client_id=execution_target.client_id,
                account=execution_target.account or data_endpoint.account,
                market_data_type=data_endpoint.market_data_type,
                risk_free_rate=config.pricing.usd_risk_free_rate,
                read_only_data_only=args.target == "data",
            )
        )
        try:
            payload = client.connect()
            print(json.dumps(payload, indent=2, default=str))
            return 0
        finally:
            client.disconnect()

    if args.command == "run-short-vol":
        config = bootstrap_runtime_config(
            config_path=args.config,
            mode=args.mode,
            venues=tuple(value.strip() for value in args.venues.split(",")) if args.venues else None,
            run_once=True if args.run_once else None,
        )
        runtime = DefinedRiskShortVolRuntime(config)

        def task() -> None:
            summary = runtime.run_cycle()
            print(json.dumps(summary, indent=2))

        return repeat_with_interval(
            task,
            interval_seconds=config.runtime.poll_seconds,
            run_once=config.runtime.run_once,
            task_name="defined-risk short-vol runtime",
        )

    parser.error(f"Unsupported command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
