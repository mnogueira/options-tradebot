from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_tradebot.config import default_settings
from options_tradebot.market import OptionContract, OptionKind, OptionQuote, OptionSnapshot
from options_tradebot.scanner import MispricingScanner


def _snapshot(
    *,
    symbol: str,
    underlying: str,
    market: str,
    currency: str,
    option_type: OptionKind,
    strike: float,
    underlying_price: float,
    implied_vol: float,
    bid: float,
    ask: float,
    volume: int,
    open_interest: int,
) -> OptionSnapshot:
    today = date(2026, 3, 24)
    return OptionSnapshot(
        contract=OptionContract(
            symbol=symbol,
            underlying=underlying,
            option_type=option_type,
            strike=strike,
            expiry=today + timedelta(days=24),
            exchange="BVMF" if market == "B3" else "SMART",
            currency=currency,
            contract_multiplier=100,
            exercise_style="european" if market == "B3" else "american",
        ),
        quote=OptionQuote(
            bid=bid,
            ask=ask,
            last=(bid + ask) / 2.0,
            volume=volume,
            open_interest=open_interest,
        ),
        timestamp=today,
        underlying_price=underlying_price,
        risk_free_rate=0.14 if market == "B3" else 0.045,
        dividend_yield=0.10 if market == "B3" else 0.03,
        implied_vol=implied_vol,
        market=market,
    )


class CrossMarketScannerTests(unittest.TestCase):
    def test_scan_cross_market_snapshots_flags_petr4_vs_pbr_vol_arb(self) -> None:
        settings = default_settings()
        scanner = MispricingScanner(settings)
        snapshots = [
            _snapshot(
                symbol="PETR4C37",
                underlying="PETR4",
                market="B3",
                currency="BRL",
                option_type=OptionKind.CALL,
                strike=37.0,
                underlying_price=36.0,
                implied_vol=0.30,
                bid=1.00,
                ask=1.08,
                volume=4_500,
                open_interest=14_000,
            ),
            _snapshot(
                symbol="PETR4P35",
                underlying="PETR4",
                market="B3",
                currency="BRL",
                option_type=OptionKind.PUT,
                strike=35.0,
                underlying_price=36.0,
                implied_vol=0.24,
                bid=0.72,
                ask=0.80,
                volume=3_800,
                open_interest=13_500,
            ),
            _snapshot(
                symbol="PBRC135",
                underlying="PBR",
                market="US",
                currency="USD",
                option_type=OptionKind.CALL,
                strike=13.5,
                underlying_price=13.1,
                implied_vol=0.25,
                bid=0.64,
                ask=0.70,
                volume=5_100,
                open_interest=19_000,
            ),
            _snapshot(
                symbol="PBRP125",
                underlying="PBR",
                market="US",
                currency="USD",
                option_type=OptionKind.PUT,
                strike=12.5,
                underlying_price=13.1,
                implied_vol=0.24,
                bid=0.54,
                ask=0.60,
                volume=4_900,
                open_interest=18_600,
            ),
            _snapshot(
                symbol="XOMC110",
                underlying="XOM",
                market="US",
                currency="USD",
                option_type=OptionKind.CALL,
                strike=110.0,
                underlying_price=108.0,
                implied_vol=0.22,
                bid=2.10,
                ask=2.22,
                volume=8_000,
                open_interest=25_000,
            ),
            _snapshot(
                symbol="XOMP105",
                underlying="XOM",
                market="US",
                currency="USD",
                option_type=OptionKind.PUT,
                strike=105.0,
                underlying_price=108.0,
                implied_vol=0.21,
                bid=1.42,
                ask=1.52,
                volume=7_200,
                open_interest=22_000,
            ),
        ]

        report = scanner.scan_cross_market_snapshots(snapshots)

        ranked_keys = {(item.underlying, item.market) for item in report.underlyings}
        self.assertIn(("PETR4", "B3"), ranked_keys)
        self.assertIn(("PBR", "US"), ranked_keys)
        self.assertIn(("XOM", "US"), ranked_keys)
        self.assertTrue(report.vol_arb_opportunities)
        top_finding = report.vol_arb_opportunities[0]
        self.assertEqual(top_finding.rich_underlying, "PETR4")
        self.assertEqual(top_finding.cheap_underlying, "PBR")
        self.assertEqual(top_finding.rich_market, "B3")
        self.assertEqual(top_finding.cheap_market, "US")
        self.assertAlmostEqual(top_finding.iv_gap, 0.05, places=6)
        self.assertEqual(top_finding.action, "SELL_PETR4_BUY_PBR")
