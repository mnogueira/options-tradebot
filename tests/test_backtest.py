from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_tradebot.config import default_settings
from options_tradebot.research.backtest import OptionBacktester


class BacktestLoopTests(unittest.TestCase):
    def test_backtest_uses_buffered_underlying_history_for_signal(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "timestamp": "2026-03-20",
                    "symbol": "MGLU3_CALL_CHEAP",
                    "underlying": "MGLU3",
                    "option_type": "call",
                    "strike": 47.0,
                    "expiry": "2026-04-07",
                    "underlying_type": "spot",
                    "contract_multiplier": 100,
                    "bid": 0.40,
                    "ask": 0.44,
                    "last": 0.42,
                    "volume": 4000,
                    "open_interest": 12000,
                    "underlying_price": 44.0,
                    "risk_free_rate": 0.14,
                    "dividend_yield": 0.10,
                    "implied_vol": 0.34,
                },
                {
                    "timestamp": "2026-03-20",
                    "symbol": "MGLU3_CALL_RICH",
                    "underlying": "MGLU3",
                    "option_type": "call",
                    "strike": 46.5,
                    "expiry": "2026-04-07",
                    "underlying_type": "spot",
                    "contract_multiplier": 100,
                    "bid": 1.50,
                    "ask": 1.62,
                    "last": 1.56,
                    "volume": 3200,
                    "open_interest": 11800,
                    "underlying_price": 44.0,
                    "risk_free_rate": 0.14,
                    "dividend_yield": 0.10,
                    "implied_vol": 0.31,
                },
                {
                    "timestamp": "2026-03-21",
                    "symbol": "MGLU3_CALL_CHEAP",
                    "underlying": "MGLU3",
                    "option_type": "call",
                    "strike": 47.0,
                    "expiry": "2026-04-07",
                    "underlying_type": "spot",
                    "contract_multiplier": 100,
                    "bid": 0.44,
                    "ask": 0.48,
                    "last": 0.46,
                    "volume": 4100,
                    "open_interest": 12100,
                    "underlying_price": 45.0,
                    "risk_free_rate": 0.14,
                    "dividend_yield": 0.10,
                    "implied_vol": 0.35,
                },
                {
                    "timestamp": "2026-03-21",
                    "symbol": "MGLU3_CALL_RICH",
                    "underlying": "MGLU3",
                    "option_type": "call",
                    "strike": 46.5,
                    "expiry": "2026-04-07",
                    "underlying_type": "spot",
                    "contract_multiplier": 100,
                    "bid": 1.56,
                    "ask": 1.68,
                    "last": 1.62,
                    "volume": 3250,
                    "open_interest": 11850,
                    "underlying_price": 45.0,
                    "risk_free_rate": 0.14,
                    "dividend_yield": 0.10,
                    "implied_vol": 0.31,
                },
                {
                    "timestamp": "2026-03-24",
                    "symbol": "MGLU3_CALL_CHEAP",
                    "underlying": "MGLU3",
                    "option_type": "call",
                    "strike": 47.0,
                    "expiry": "2026-04-07",
                    "underlying_type": "spot",
                    "contract_multiplier": 100,
                    "bid": 0.48,
                    "ask": 0.52,
                    "last": 0.50,
                    "volume": 4200,
                    "open_interest": 12200,
                    "underlying_price": 46.0,
                    "risk_free_rate": 0.14,
                    "dividend_yield": 0.10,
                    "implied_vol": 0.36,
                },
                {
                    "timestamp": "2026-03-24",
                    "symbol": "MGLU3_CALL_RICH",
                    "underlying": "MGLU3",
                    "option_type": "call",
                    "strike": 46.5,
                    "expiry": "2026-04-07",
                    "underlying_type": "spot",
                    "contract_multiplier": 100,
                    "bid": 1.60,
                    "ask": 1.72,
                    "last": 1.68,
                    "volume": 3300,
                    "open_interest": 11900,
                    "underlying_price": 46.0,
                    "risk_free_rate": 0.14,
                    "dividend_yield": 0.10,
                    "implied_vol": 0.31,
                },
            ]
        )
        result = OptionBacktester(default_settings()).run(frame, output_dir="runtime/test_backtest_history")
        self.assertEqual(result.equity_curve.iloc[-1]["signal_action"], "BUY_CALL")
