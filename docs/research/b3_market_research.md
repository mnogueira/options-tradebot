# B3 Options Market Research

## Scope

This document captures the market structure research used to choose the initial B3 universe and shape the bot's constraints.

## Verified Market Findings

### 1. Liquidity is concentrated

B3 reported on February 28, 2026 that:

- average daily turnover in monthly equity options reached `R$1.2 billion`
- weekly equity options reached `R$138 million`
- five companies represented `78%` of equity-option volume
- calls represented `59%` of turnover

Source:

- https://www.b3.com.br/pt_br/noticias/mercado-de-opcoes.htm

### 2. Weekly options matter, but concentration is still high

B3's one-year review of weekly options reported that the market share by contracts was led by:

- `BOVA11` with `34%`
- `PETR4` with `28%`
- `VALE3` with `11%`

Source:

- https://www.b3.com.br/pt_br/noticias/equities.htm

### 3. Underlying liquidity leaders

The B3 Week Options Masterclass deck for 2025 highlighted the top traded underlyings by average daily turnover:

- `BOVA11`: `R$206 million`
- `PETR4`: `R$117 million`
- `VALE3`: `R$82 million`

The same deck also showed how fast spreads widen outside the best names:

- top-3 monthly average top-of-book spread: `6%`
- top-5 monthly: `9%`
- top-3 weekly: `19%`
- top-5 weekly: `29%`

Source:

- https://content.b3.com.br/wp-content/uploads/2025/09/B3-Week-Options-Masterclass-2.pdf

### 4. Product characteristics

B3's product pages confirm:

- weekly options expire every Friday except the third Friday of the month
- stock and ETF options can be American or European
- puts are European in the current product descriptions
- contract size is `100` shares

Sources:

- https://www.b3.com.br/pt_br/produtos-e-servicos/negociacao/renda-variavel/opcoes-sobre-acoes.htm
- https://www.b3.com.br/pt_br/produtos-e-servicos/negociacao/renda-variavel/opcoes-semanais-de-acoes-units-etfs-e-indices.htm

### 5. B3 option pricing guidance

B3's option-pricing manual is important for model selection:

- liquid options are priced from observed implied volatility
- less liquid names can use GARCH volatility forecasts plus Corrado-Su adjustments

Source:

- https://www.b3.com.br/data/files/66/F0/A6/7C/34525610BE423F46AC094EA8/Manual-de-Aprecamento-Opcoes.pdf

## User-Supplied Orchestrator Intel

The user also supplied March 24, 2026 market intel that was not independently reproduced through public sources in this session but is reasonable enough to preserve as research input:

- PETR4 and VALE3 both showing roughly an `8-point` IV premium over realized volatility
- PETR4 and VALE3 as the most actionable single-stock names
- a high-volatility macro regime linked to oil and geopolitical stress
- very high current attention on short-vol opportunities

Those items are treated as tactical hypotheses rather than fully audited facts inside the codebase.

## Initial Universe Decision

The live universe for version `0.1.0` is:

1. `PETR4`
2. `VALE3`
3. `BOVA11`
4. `WDO` options only after broker-side liquidity validation

Why:

- `PETR4` and `VALE3` are consistently present in both concentration and weekly-activity evidence
- `BOVA11` is the ETF liquidity anchor and is valuable for weekly-tenor coverage
- `WDO` options remain interesting, but this run did not gather enough public liquidity evidence to make them a first-class default

## Data Sources

Public and practical sources wired into the project workflow:

- B3 open positions:
  - https://www.b3.com.br/pt_br/market-data-e-indices/servicos-de-dados/market-data/consultas/mercado-a-vista/opcoes/posicoes-em-aberto/
- B3 statistics:
  - https://www.b3.com.br/en_us/products-and-services/trading/equities/cash-equities/statistics.htm
- B3 public reference snapshot in this repo:
  - [data/reference/b3_options_liquidity_snapshot_2025_2026.csv](/C:/Dev/options-tradebot/data/reference/b3_options_liquidity_snapshot_2025_2026.csv)
- MT5 broker mapping template:
  - [data/reference/mt5_option_symbols.template.csv](/C:/Dev/options-tradebot/data/reference/mt5_option_symbols.template.csv)

## Modeling Decision

Version `0.1.0` uses:

- Black-Scholes as the default pricing engine for equity and ETF options
- Black-76 for futures-style options
- a liquidity-weighted local vol surface as the first practical surface model
- Corrado-Su and GARCH helpers for less-liquid or sparse-chain scenarios

The next improvement should be an SVI calibration path once clean per-strike historical chains are available.
