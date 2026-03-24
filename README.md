# options-tradebot

Systematic options tradebot for the Brazilian market with a research-first workflow, fair-value pricing, small-capital sizing, paper trading, and MetaTrader 5 integration.

## What This Project Does

The project turns a Black-Scholes-style mispricing idea into a B3-specific workflow:

- rank liquid B3 options by fair-value dislocation
- fit a liquidity-aware volatility surface
- size trades for small accounts using 1 to 5 contracts
- paper trade through a journaled service loop
- connect to MT5 for data collection and later demo/live order routing

The first production universe is:

- `PETR4`
- `VALE3`
- `BOVA11`
- `WDO` options only when broker-side liquidity is confirmed in MT5

## Strategy Foundation

The user supplied the source video: `How to Trade with the Black-Scholes Model` by Roman Paolucci.

The trading idea used here is:

- estimate fair option value from a pricing model and a smooth implied-vol surface
- compare fair value with tradable quotes instead of theoretical midpoints
- require the edge to clear spread and liquidity frictions
- express the trade in a way that still makes sense for small capital

This repo currently implements the single-leg fair-value engine and the paper-trading runtime. The documentation also outlines when high-IV environments such as PETR4 can justify migrating to defined-risk spread overlays like bull call spreads and butterflies.

## Current Status

Implemented:

- Python package with CLI entrypoints
- Black-Scholes, Black-76, implied vol inversion, Corrado-Su helper, and GARCH-style vol forecast
- liquidity-weighted volatility surface calibration
- fair-value signal generation
- small-capital position sizing with Greek caps
- paper broker and journaled service loop
- MT5 data probe, snapshot collection, and order-routing adapters
- reference market research docs and datasets
- unit tests for pricing, sizing, and signal selection

Environment-blocked during this run:

- direct YouTube transcript fetch from Python was blocked by sandbox network restrictions
- MT5 Python package is installed, but the local terminal was not responding over IPC during this session
- GitHub CLI is installed, but `gh auth status` reported an invalid token, so remote repo creation/push cannot finish until authentication is fixed

## Quickstart

### 1. Create a virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

### 2. Probe MT5

```powershell
python scripts/probe_mt5.py --mt5-path "C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"
```

### 3. Build an MT5 option mapping

Edit [data/reference/mt5_option_symbols.template.csv](/C:/Dev/options-tradebot/data/reference/mt5_option_symbols.template.csv) with your broker's exact MT5 option symbols.

### 4. Collect snapshots

```powershell
python scripts/collect_mt5_options_data.py `
  --mapping data/reference/mt5_option_symbols.template.csv `
  --output data/runtime/latest_option_snapshots.csv `
  --mt5-path "C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"
```

### 5. Run research summary

```powershell
python scripts/run_option_research.py --snapshots data/runtime/latest_option_snapshots.csv
```

### 6. Run a backtest

```powershell
python scripts/run_backtest.py --snapshots data/runtime/latest_option_snapshots.csv --output-dir runtime/backtest
```

### 7. Run one paper-trading cycle

```powershell
python scripts/run_paper_trade.py --snapshots data/runtime/latest_option_snapshots.csv --output-dir runtime/paper
```

## CLI

```powershell
python -m options_tradebot.cli.main mt5-probe --mt5-path "C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"
python -m options_tradebot.cli.main research-summary --snapshots data/runtime/latest_option_snapshots.csv
python -m options_tradebot.cli.main backtest --snapshots data/runtime/latest_option_snapshots.csv
python -m options_tradebot.cli.main paper --snapshots data/runtime/latest_option_snapshots.csv
```

## Project Layout

- [src/options_tradebot](/C:/Dev/options-tradebot/src/options_tradebot)
- [scripts](/C:/Dev/options-tradebot/scripts)
- [tests](/C:/Dev/options-tradebot/tests)
- [docs/research](/C:/Dev/options-tradebot/docs/research)
- [docs/architecture](/C:/Dev/options-tradebot/docs/architecture)
- [data/reference](/C:/Dev/options-tradebot/data/reference)

## Key Docs

- [docs/research/video_strategy_adaptation.md](/C:/Dev/options-tradebot/docs/research/video_strategy_adaptation.md)
- [docs/research/b3_market_research.md](/C:/Dev/options-tradebot/docs/research/b3_market_research.md)
- [docs/architecture/system_design.md](/C:/Dev/options-tradebot/docs/architecture/system_design.md)

## GitHub

The requested GitHub step is:

```powershell
gh repo create options-tradebot --public --source=. --remote=origin
```

That command is ready to run once `gh auth login` succeeds in your environment.
