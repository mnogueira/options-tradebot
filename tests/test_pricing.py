from __future__ import annotations

import unittest

from options_tradebot.market import OptionKind, black_scholes_price, implied_volatility


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


if __name__ == "__main__":
    unittest.main()
