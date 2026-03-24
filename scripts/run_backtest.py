"""Run the options backtester on a snapshot CSV."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from options_tradebot.cli.main import main


if __name__ == "__main__":
    sys.argv = [sys.argv[0], "backtest", *sys.argv[1:]]
    raise SystemExit(main())
