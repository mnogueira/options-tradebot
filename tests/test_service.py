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
from options_tradebot.execution import PaperBroker, PaperTradingService
from options_tradebot.market import OptionContract, OptionKind, OptionQuote, OptionSnapshot, black_scholes_greeks
from options_tradebot.strategies import StrategySignal


class ServiceRiskTests(unittest.TestCase):
    def test_aggregate_greeks_uses_stored_position_greeks(self) -> None:
        today = date(2026, 3, 24)
        snapshot = OptionSnapshot(
            contract=OptionContract(
                symbol="PETR4C1",
                underlying="PETR4",
                option_type=OptionKind.CALL,
                strike=47.0,
                expiry=today + timedelta(days=14),
            ),
            quote=OptionQuote(bid=0.48, ask=0.52, last=0.50, volume=4000, open_interest=12000),
            timestamp=today,
            underlying_price=46.0,
            risk_free_rate=0.14,
            dividend_yield=0.10,
            implied_vol=0.36,
        )
        greeks = black_scholes_greeks(
            spot=46.0,
            strike=47.0,
            time_to_expiry=snapshot.time_to_expiry,
            rate=0.14,
            dividend_yield=0.10,
            volatility=0.36,
            option_type=OptionKind.CALL,
        )
        signal = StrategySignal(
            action="BUY_CALL",
            contract_symbol="PETR4C1",
            underlying="PETR4",
            contracts=2,
            entry_price=0.52,
            target_price=0.68,
            stop_price=0.43,
            fair_value=0.68,
            fair_volatility=0.36,
            reason="approved",
            greeks=greeks,
            score=1.0,
        )
        broker = PaperBroker(initial_cash=10_000.0)
        self.assertTrue(broker.open_position(signal, snapshot))
        service = PaperTradingService(settings=default_settings(), broker=broker, output_dir="runtime/test_service")
        aggregate = service.aggregate_greeks()
        self.assertIsNotNone(aggregate)
        self.assertAlmostEqual(aggregate.delta, greeks.delta * 200, places=6)
        self.assertAlmostEqual(aggregate.gamma, greeks.gamma * 200, places=6)
        self.assertAlmostEqual(aggregate.vega, greeks.vega * 200, places=6)
        self.assertAlmostEqual(aggregate.theta, greeks.theta * 200, places=6)

    def test_daily_loss_circuit_breaker_blocks_new_positions(self) -> None:
        settings = default_settings()
        service = PaperTradingService(settings=settings, output_dir="runtime/test_service")
        service.broker.cash = 9_600.0
        service._peak_equity = 10_000.0
        service._daily_anchor_date = date(2026, 3, 24)
        service._daily_start_equity = 10_000.0
        today = date(2026, 3, 24)
        expiry = today + timedelta(days=14)
        history = pd.Series(
            [44.0, 45.0, 46.0],
            index=pd.to_datetime(["2026-03-20", "2026-03-21", "2026-03-24"]),
        )
        chain = [
            OptionSnapshot(
                contract=OptionContract(
                    symbol="MGLU3_CALL_CHEAP",
                    underlying="MGLU3",
                    option_type=OptionKind.CALL,
                    strike=47.0,
                    expiry=expiry,
                ),
                quote=OptionQuote(bid=0.48, ask=0.52, last=0.50, volume=4200, open_interest=12200),
                timestamp=today,
                underlying_price=46.0,
                risk_free_rate=0.14,
                dividend_yield=0.10,
                implied_vol=0.36,
            ),
            OptionSnapshot(
                contract=OptionContract(
                    symbol="MGLU3_CALL_RICH",
                    underlying="MGLU3",
                    option_type=OptionKind.CALL,
                    strike=46.5,
                    expiry=expiry,
                ),
                quote=OptionQuote(bid=1.60, ask=1.72, last=1.68, volume=3300, open_interest=11900),
                timestamp=today,
                underlying_price=46.0,
                risk_free_rate=0.14,
                dividend_yield=0.10,
                implied_vol=0.31,
            ),
        ]
        result = service.run_once(chain, underlying_histories={"MGLU3": history})
        self.assertEqual(result.signal.action, "HOLD")
        self.assertEqual(result.signal.reason, "max_daily_loss_breached")
        self.assertFalse(result.opened)
