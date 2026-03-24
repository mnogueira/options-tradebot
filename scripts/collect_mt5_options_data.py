"""Collect option snapshots from MT5 using a broker-specific symbol mapping."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from options_tradebot.data import MT5ConnectionConfig, MT5MarketDataClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect MT5 option snapshots into CSV.")
    parser.add_argument("--mapping", required=True, help="CSV with MT5 option symbols and metadata.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--mt5-path")
    parser.add_argument("--mt5-login", type=int)
    parser.add_argument("--mt5-password")
    parser.add_argument("--mt5-server")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    client = MT5MarketDataClient(
        MT5ConnectionConfig(
            path=args.mt5_path,
            login=args.mt5_login,
            password=args.mt5_password,
            server=args.mt5_server,
        )
    )
    try:
        client.connect()
        snapshots = client.snapshots_from_mapping(args.mapping)
    finally:
        client.shutdown()
    frame = pd.DataFrame(
        [
            {
                "timestamp": snapshot.timestamp.isoformat(),
                "symbol": snapshot.contract.symbol,
                "underlying": snapshot.contract.underlying,
                "option_type": snapshot.contract.option_type.value,
                "strike": snapshot.contract.strike,
                "expiry": snapshot.contract.expiry.isoformat(),
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
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
