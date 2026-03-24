# System Design

## Objective

Build a researchable and operable options bot for B3 that can move from CSV-based research into MT5 paper trading and eventually demo/live execution.

## Architecture

### Market layer

- [src/options_tradebot/market/models.py](/C:/Dev/options-tradebot/src/options_tradebot/market/models.py)
- [src/options_tradebot/market/pricing.py](/C:/Dev/options-tradebot/src/options_tradebot/market/pricing.py)
- [src/options_tradebot/market/surface.py](/C:/Dev/options-tradebot/src/options_tradebot/market/surface.py)

Responsibilities:

- represent option contracts and quotes
- compute Black-Scholes and Black-76 prices
- compute Greeks and implied vol
- fit a liquidity-aware local surface

### Data layer

- [src/options_tradebot/data/models.py](/C:/Dev/options-tradebot/src/options_tradebot/data/models.py)
- [src/options_tradebot/data/mt5_client.py](/C:/Dev/options-tradebot/src/options_tradebot/data/mt5_client.py)

Responsibilities:

- load snapshot datasets from CSV
- connect to MT5
- probe terminal availability
- collect bars and option quotes from broker-specific symbol mappings

### Strategy layer

- [src/options_tradebot/strategies/fair_value.py](/C:/Dev/options-tradebot/src/options_tradebot/strategies/fair_value.py)

Responsibilities:

- infer directional regime
- evaluate candidates by fair-value edge
- enforce liquidity, delta, spread, and premium filters
- output a tradeable signal sized for small capital

### Risk layer

- [src/options_tradebot/risk/sizing.py](/C:/Dev/options-tradebot/src/options_tradebot/risk/sizing.py)

Responsibilities:

- cap trade count to 1 to 5 contracts
- keep premium risk bounded
- respect aggregate Greek limits

### Execution layer

- [src/options_tradebot/execution/paper.py](/C:/Dev/options-tradebot/src/options_tradebot/execution/paper.py)
- [src/options_tradebot/execution/service.py](/C:/Dev/options-tradebot/src/options_tradebot/execution/service.py)
- [src/options_tradebot/brokers/mt5_execution.py](/C:/Dev/options-tradebot/src/options_tradebot/brokers/mt5_execution.py)

Responsibilities:

- mark open positions
- evaluate stop, target, and expiry exits
- persist paper state and signal logs
- expose a live MT5 order-routing adapter for demo or production

### Research layer

- [src/options_tradebot/research/liquidity.py](/C:/Dev/options-tradebot/src/options_tradebot/research/liquidity.py)
- [src/options_tradebot/research/backtest.py](/C:/Dev/options-tradebot/src/options_tradebot/research/backtest.py)

Responsibilities:

- summarize datasets by liquidity and spread
- replay historical snapshots through the service loop
- write equity curves and trade logs

## Operating Modes

### Research mode

- load CSV chains
- summarize spreads and participation
- inspect surface fit quality

### Backtest mode

- replay historical snapshots
- let the same strategy and paper broker produce trades

### Paper mode

- ingest current snapshots
- score live opportunities
- journal state to disk

### Live mode

- still gated
- requires verified MT5 demo connectivity
- should remain demo-only until broker routing, slippage capture, and exercise-risk handling are validated

## Known Gaps

- no production SVI calibration yet
- no multi-leg structure executor yet
- no broker-agnostic B3 option-chain normalizer yet
- no automatic corporate-action or dividend feed yet
- exercise and assignment edge cases still need a dedicated expiration-day module
