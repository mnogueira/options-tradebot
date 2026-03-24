from __future__ import annotations

from datetime import date, timedelta
from math import exp, log, sqrt
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_tradebot.market import OptionContract, OptionKind, OptionQuote, OptionSnapshot, calibrate_surface


class SurfaceTests(unittest.TestCase):
    def test_calibrate_surface_uses_svi_when_enough_points(self) -> None:
        today = date(2026, 3, 24)
        expiry = today + timedelta(days=21)
        time_to_expiry = 21 / 252
        spot = 46.0
        rate = 0.14
        dividend_yield = 0.10
        forward = spot * exp((rate - dividend_yield) * time_to_expiry)
        a, b, rho, m, sigma = 0.01, 0.12, -0.35, 0.0, 0.18
        chain: list[OptionSnapshot] = []
        for index, strike in enumerate([40.0, 42.0, 44.0, 46.0, 48.0, 50.0, 52.0], start=1):
            moneyness = log(strike / forward)
            total_variance = a + b * (rho * (moneyness - m) + sqrt((moneyness - m) ** 2 + sigma**2))
            implied_vol = sqrt(max(total_variance, 1e-8) / time_to_expiry)
            chain.append(
                OptionSnapshot(
                    contract=OptionContract(
                        symbol=f"PETR4C{index}",
                        underlying="PETR4",
                        option_type=OptionKind.CALL,
                        strike=strike,
                        expiry=expiry,
                    ),
                    quote=OptionQuote(
                        bid=1.00 + index * 0.05,
                        ask=1.05 + index * 0.05,
                        last=1.02 + index * 0.05,
                        volume=3_000 + index * 100,
                        open_interest=10_000 + index * 250,
                    ),
                    timestamp=today,
                    underlying_price=spot,
                    risk_free_rate=rate,
                    dividend_yield=dividend_yield,
                    implied_vol=implied_vol,
                )
            )
        surface, diagnostics = calibrate_surface(chain, method="svi", min_points=5)
        self.assertEqual(diagnostics.model_name, "svi")
        fitted = surface.volatility_for_snapshot(chain[3])
        self.assertAlmostEqual(fitted, chain[3].implied_vol or 0.0, delta=0.05)
