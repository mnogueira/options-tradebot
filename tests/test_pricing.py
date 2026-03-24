from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_tradebot.market import (
    OptionKind,
    black_scholes_price,
    garch11_forecast_volatility,
    implied_volatility,
)


class PricingTests(unittest.TestCase):
    def test_implied_volatility_round_trip(self) -> None:
        price = black_scholes_price(
            spot=46.0,
            strike=48.0,
            time_to_expiry=14 / 365,
            rate=0.14,
            dividend_yield=0.10,
            volatility=0.30,
            option_type=OptionKind.CALL,
        )
        fitted = implied_volatility(
            market_price=price,
            spot=46.0,
            strike=48.0,
            time_to_expiry=14 / 365,
            rate=0.14,
            dividend_yield=0.10,
            option_type=OptionKind.CALL,
        )
        self.assertIsNotNone(fitted)
        self.assertAlmostEqual(fitted or 0.0, 0.30, places=3)

    def test_garch_forecast_respects_omega_parameter(self) -> None:
        returns = [0.01, -0.015, 0.02, -0.01, 0.012, -0.018]
        low_omega = garch11_forecast_volatility(returns, omega=1e-6, alpha=0.08, beta=0.90)
        high_omega = garch11_forecast_volatility(returns, omega=1e-4, alpha=0.08, beta=0.90)
        self.assertGreater(high_omega, low_omega)


if __name__ == "__main__":
    unittest.main()
