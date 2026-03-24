# Video Strategy Adaptation

## Source Video

- Title: `How to Trade with the Black-Scholes Model`
- Creator: Roman Paolucci / Quant Guild
- URL: https://youtu.be/0x-Pc-Z3wu4?si=EnEMSS436G7MWynN

## Transcript Status

The direct `youtube-transcript-api` fetch was attempted from the local Python environment on March 24, 2026 and failed because outbound sockets are blocked in this sandbox. The strategy notes below therefore combine:

- the user-supplied identification of the video
- a web summary of the exact video
- related Quant Guild material on algorithmic options market making

## Extracted Idea

The core idea is a practical Black-Scholes trading workflow:

- compute theoretical option value from the pricing inputs
- compare fair value with market prices
- use Greeks to understand directional and volatility risk
- exploit persistent pricing dislocations instead of making one-off predictions

The original framing appears closer to market-making and statistical edge harvesting than to discretionary chart trading.

## Why It Must Be Adapted For Brazil

The original idea does not assume the same constraints we face here:

- B3 liquidity is concentrated in a small set of underlyings
- weekly options can be useful but spreads widen quickly outside the best names
- small capital cannot rely on spraying large numbers of contracts
- naked short-vol structures can create margin pressure that makes a good idea unusable for retail-sized accounts

## Adaptation Used In This Repo

The bot uses a layered adaptation:

1. Universe restriction
- Trade only the most liquid B3 names first: `PETR4`, `VALE3`, `BOVA11`
- Treat `WDO` options as opportunistic and only if MT5 exposes a reliable chain

2. Fair-value engine
- Use Black-Scholes for equity and ETF options
- Use Black-76 for futures-style options
- Fit a smooth implied-vol surface from observed quotes
- Blend surface IV with realized and GARCH-style forecast vol

3. Entry logic
- Only act when fair value beats the tradable ask by more than an edge threshold
- Require sufficient open interest, volume, and acceptable spread percentage
- Restrict candidates to moderate-delta options and near expiries where B3 liquidity is strongest

4. Small-capital controls
- Cap trade count at 1 to 5 contracts
- Limit premium outlay per trade
- Enforce aggregate Greek caps

5. Structure migration
- In high-IV regimes such as PETR4 event-driven stress, the same signal engine can be upgraded into defined-risk structures like bull call spreads or butterflies
- The single-leg runtime is implemented first because it keeps research, pricing, and paper-routing simpler to verify

## March 2026 Overlay

The user supplied live March 24, 2026 intel pointing to elevated implied volatility in PETR4 and VALE3. That matters because:

- fair-value dislocations can come from both smile shape and outright IV premium
- single-leg longs are not always the best expression when IV is already rich
- a production deployment should compare two playbooks:
  - long-premium when quoted options are cheap relative to the local surface
  - defined-risk premium selling when quoted IV is rich relative to realized and forecast vol

This repo implements the first playbook directly and documents the second as the next structural extension.
