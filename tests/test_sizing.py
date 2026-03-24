from __future__ import annotations

import unittest

from options_tradebot.market.models import GreekVector
from options_tradebot.risk import GreekLimits, size_option_position


class SizingTests(unittest.TestCase):
    def test_sizing_respects_max_contracts(self) -> None:
        decision = size_option_position(
            account_equity=100_000.0,
            premium=1.0,
            contract_multiplier=100,
            risk_per_trade_pct=0.10,
            max_contracts=5,
            greek_limits=GreekLimits(),
            current_portfolio_greeks=None,
            candidate_greeks=GreekVector(delta=5.0, gamma=0.1, vega=5.0, theta=-1.0),
        )
        self.assertFalse(decision.rejected)
        self.assertEqual(decision.contracts, 5)


if __name__ == "__main__":
    unittest.main()
