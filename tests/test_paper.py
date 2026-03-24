from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_tradebot.execution import PaperBroker
from options_tradebot.market.models import OptionContract, OptionKind, OptionQuote, OptionSnapshot
from options_tradebot.strategies import StrategySignal


class PaperBrokerTests(unittest.TestCase):
    def test_open_and_close_position_updates_cash(self) -> None:
        today = date(2026, 3, 24)
        snapshot = OptionSnapshot(
            contract=OptionContract(
                symbol="PETR4P1",
                underlying="PETR4",
                option_type=OptionKind.PUT,
                strike=35.0,
                expiry=today + timedelta(days=10),
            ),
            quote=OptionQuote(bid=1.80, ask=2.00, last=1.90, volume=2000, open_interest=7000),
            timestamp=today,
            underlying_price=34.0,
            risk_free_rate=0.14,
            implied_vol=0.22,
        )
        broker = PaperBroker(initial_cash=5_000.0)
        signal = StrategySignal(
            action="BUY_PUT",
            contract_symbol="PETR4P1",
            underlying="PETR4",
            contracts=2,
            entry_price=2.00,
            target_price=2.50,
            stop_price=1.60,
            fair_value=2.40,
            fair_volatility=0.24,
            reason="approved",
            score=1.0,
        )
        self.assertTrue(broker.open_position(signal, snapshot))
        self.assertAlmostEqual(broker.cash, 4_600.0)
        exit_snapshot = OptionSnapshot(
            contract=snapshot.contract,
            quote=OptionQuote(bid=2.50, ask=2.60, last=2.55, volume=2000, open_interest=7000),
            timestamp=today,
            underlying_price=33.5,
            risk_free_rate=0.14,
            implied_vol=0.25,
        )
        trade = broker.close_position(exit_snapshot, "target")
        self.assertGreater(trade.pnl, 0.0)
        self.assertAlmostEqual(broker.cash, 5_100.0)


if __name__ == "__main__":
    unittest.main()
