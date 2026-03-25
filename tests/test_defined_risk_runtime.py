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

from options_tradebot.config.loader import load_short_vol_config
from options_tradebot.market.models import OptionContract, OptionKind, OptionQuote, OptionSnapshot
from options_tradebot.portfolio.state import PortfolioState
from options_tradebot.runtime.position_monitor import evaluate_open_positions
from options_tradebot.strategies.defined_risk.trade_selector import rank_defined_risk_candidates
from options_tradebot.strategies.defined_risk.types import ManagedPosition


def _snapshot(
    *,
    symbol: str,
    option_type: OptionKind,
    strike: float,
    bid: float,
    ask: float,
    implied_vol: float,
    volume: int,
    open_interest: int,
) -> OptionSnapshot:
    today = date(2026, 3, 25)
    return OptionSnapshot(
        contract=OptionContract(
            symbol=symbol,
            underlying="PBR",
            option_type=option_type,
            strike=strike,
            expiry=today + timedelta(days=21),
            exchange="SMART",
            currency="USD",
            contract_multiplier=100,
            exercise_style="american",
        ),
        quote=OptionQuote(
            bid=bid,
            ask=ask,
            last=(bid + ask) / 2.0,
            volume=volume,
            open_interest=open_interest,
        ),
        timestamp=today,
        underlying_price=100.0,
        risk_free_rate=0.045,
        implied_vol=implied_vol,
        market="US",
    )


class DefinedRiskRuntimeTests(unittest.TestCase):
    def test_rank_defined_risk_candidates_returns_all_three_strategy_families(self) -> None:
        config = load_short_vol_config(ROOT / "config" / "defined_risk_short_vol.toml")
        chain = [
            _snapshot(symbol="PBR_P_95", option_type=OptionKind.PUT, strike=95.0, bid=1.80, ask=1.92, implied_vol=0.36, volume=5000, open_interest=12000),
            _snapshot(symbol="PBR_P_90", option_type=OptionKind.PUT, strike=90.0, bid=0.72, ask=0.82, implied_vol=0.31, volume=3500, open_interest=9000),
            _snapshot(symbol="PBR_P_85", option_type=OptionKind.PUT, strike=85.0, bid=0.28, ask=0.36, implied_vol=0.28, volume=2400, open_interest=7000),
            _snapshot(symbol="PBR_C_105", option_type=OptionKind.CALL, strike=105.0, bid=1.76, ask=1.88, implied_vol=0.35, volume=5200, open_interest=12500),
            _snapshot(symbol="PBR_C_110", option_type=OptionKind.CALL, strike=110.0, bid=0.68, ask=0.78, implied_vol=0.30, volume=3300, open_interest=8800),
            _snapshot(symbol="PBR_C_115", option_type=OptionKind.CALL, strike=115.0, bid=0.24, ask=0.32, implied_vol=0.27, volume=2200, open_interest=6500),
        ]
        history = pd.Series(
            [92.0 + index * 0.18 for index in range(60)],
            index=pd.date_range("2026-01-20", periods=60, freq="D"),
        )

        ranked = rank_defined_risk_candidates(venue="ib", chain=chain, underlying_history=history, config=config)

        strategy_names = {candidate.strategy_name for candidate in ranked}
        self.assertIn("bull_put_spread", strategy_names)
        self.assertIn("bear_call_spread", strategy_names)
        self.assertIn("iron_condor", strategy_names)
        self.assertTrue(all(candidate.score > 0 for candidate in ranked))

    def test_position_monitor_flags_target_exit(self) -> None:
        config = load_short_vol_config(ROOT / "config" / "defined_risk_short_vol.toml")
        chain = [
            _snapshot(symbol="PBR_P_95", option_type=OptionKind.PUT, strike=95.0, bid=1.80, ask=1.92, implied_vol=0.36, volume=5000, open_interest=12000),
            _snapshot(symbol="PBR_P_90", option_type=OptionKind.PUT, strike=90.0, bid=0.72, ask=0.82, implied_vol=0.31, volume=3500, open_interest=9000),
            _snapshot(symbol="PBR_C_105", option_type=OptionKind.CALL, strike=105.0, bid=1.76, ask=1.88, implied_vol=0.35, volume=5200, open_interest=12500),
            _snapshot(symbol="PBR_C_110", option_type=OptionKind.CALL, strike=110.0, bid=0.68, ask=0.78, implied_vol=0.30, volume=3300, open_interest=8800),
        ]
        history = pd.Series(
            [92.0 + index * 0.18 for index in range(60)],
            index=pd.date_range("2026-01-20", periods=60, freq="D"),
        )
        ranked = rank_defined_risk_candidates(venue="ib", chain=chain, underlying_history=history, config=config)
        bull_put = next(candidate for candidate in ranked if candidate.strategy_name == "bull_put_spread").with_contracts(2)
        position = ManagedPosition.from_candidate(bull_put, mode="sim")
        snapshot_map = {
            "ib": {
                "PBR_P_95": _snapshot(symbol="PBR_P_95", option_type=OptionKind.PUT, strike=95.0, bid=0.10, ask=0.12, implied_vol=0.22, volume=4000, open_interest=11000),
                "PBR_P_90": _snapshot(symbol="PBR_P_90", option_type=OptionKind.PUT, strike=90.0, bid=0.01, ask=0.03, implied_vol=0.18, volume=3000, open_interest=8000),
            }
        }

        updated, exits = evaluate_open_positions(
            portfolio_state=PortfolioState(open_positions=(position,), closed_positions=()),
            snapshot_maps=snapshot_map,
            config=config,
        )

        self.assertEqual(updated, [])
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0][2], "target")


if __name__ == "__main__":
    unittest.main()
