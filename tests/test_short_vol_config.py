from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_tradebot.config.loader import load_short_vol_config


class ShortVolConfigTests(unittest.TestCase):
    def test_load_short_vol_config_reads_toml(self) -> None:
        config = load_short_vol_config(ROOT / "config" / "defined_risk_short_vol.toml")
        self.assertEqual(config.app.mode, "sim")
        self.assertIn("mt5", config.app.venues)
        self.assertIn("ib", config.app.venues)
        self.assertGreater(config.risk.capital_base, 0.0)
        self.assertEqual(config.discovery.ib.exchange, "SMART")
        self.assertEqual(config.discovery.ib.currency, "USD")
        self.assertGreater(config.discovery.ib.scanner_max_results, 0)
        self.assertTrue(config.strategies.bull_put.enabled)
        self.assertTrue(config.strategies.bear_call.enabled)
        self.assertTrue(config.strategies.iron_condor.enabled)


if __name__ == "__main__":
    unittest.main()
