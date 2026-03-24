from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sys
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_tradebot.config import default_settings
from options_tradebot.market import GreekVector, OptionContract, OptionKind, OptionQuote, OptionSnapshot
from options_tradebot.strategies import FairValueOptionsStrategy


class StrategyTests(unittest.TestCase):
    def test_strategy_prefers_underpriced_bullish_call(self) -> None:
        settings = default_settings()
        strategy = FairValueOptionsStrategy(settings)
        today = date(2026, 3, 24)
        expiry = today + timedelta(days=14)
        history = pd.Series(
            [40 + index * 0.4 for index in range(30)],
            index=pd.date_range("2026-02-20", periods=30, freq="D"),
        )
        cheap_call = OptionSnapshot(
            contract=OptionContract(
                symbol="PETR4_CALL_CHEAP",
                underlying="PETR4",
                option_type=OptionKind.CALL,
                strike=47.0,
                expiry=expiry,
            ),
            quote=OptionQuote(bid=0.48, ask=0.52, last=0.50, volume=4_000, open_interest=12_000),
            timestamp=today,
            underlying_price=46.0,
            risk_free_rate=0.14,
            dividend_yield=0.10,
            implied_vol=0.36,
        )
        rich_call = OptionSnapshot(
            contract=OptionContract(
                symbol="PETR4_CALL_RICH",
                underlying="PETR4",
                option_type=OptionKind.CALL,
                strike=46.5,
                expiry=expiry,
            ),
            quote=OptionQuote(bid=1.60, ask=1.72, last=1.68, volume=3_000, open_interest=11_500),
            timestamp=today,
            underlying_price=46.0,
            risk_free_rate=0.14,
            dividend_yield=0.10,
            implied_vol=0.31,
        )
        signal = strategy.select_signal(
            chain=[cheap_call, rich_call],
            underlying_history=history,
            account_equity=10_000.0,
        )
        self.assertEqual(signal.action, "BUY_CALL")
        self.assertEqual(signal.contract_symbol, "PETR4_CALL_CHEAP")
        self.assertGreaterEqual(signal.contracts, 1)

    def test_strategy_uses_broker_greeks_for_delta_filter(self) -> None:
        settings = default_settings()
        strategy = FairValueOptionsStrategy(settings)
        today = date(2026, 3, 24)
        expiry = today + timedelta(days=14)
        history = pd.Series(
            [40 + index * 0.5 for index in range(30)],
            index=pd.date_range("2026-02-20", periods=30, freq="D"),
        )
        broker_delta_call = OptionSnapshot(
            contract=OptionContract(
                symbol="PBR_CALL_IB",
                underlying="PBR",
                option_type=OptionKind.CALL,
                strike=70.0,
                expiry=expiry,
                exchange="SMART",
                currency="USD",
            ),
            quote=OptionQuote(bid=0.95, ask=1.00, last=0.98, volume=6_000, open_interest=18_000),
            timestamp=today,
            underlying_price=100.0,
            risk_free_rate=0.045,
            implied_vol=0.28,
            market="US",
            broker_greeks=GreekVector(delta=0.40, gamma=0.03, vega=0.11, theta=-0.02),
        )
        signal = strategy.select_signal(
            chain=[broker_delta_call],
            underlying_history=history,
            account_equity=10_000.0,
        )
        self.assertEqual(signal.action, "BUY_CALL")
        self.assertEqual(signal.contract_symbol, "PBR_CALL_IB")


if __name__ == "__main__":
    unittest.main()
