# Defined-Risk Short Vol Implementation Plan

## Objetivo

Transformar o repositorio em um sistema unico de monitoramento, selecao, paper trading real e live trading para estrategias de opcoes `defined-risk short vol`, com foco inicial em:

- `bull put spreads`
- `bear call spreads`
- `iron condors`

O sistema deve funcionar de forma consistente em:

- `MT5` para instrumentos disponiveis no broker conectado
- `Interactive Brokers` com:
  - dados de mercado e monitoramento via sessao apropriada
  - execucao em `paper account`
  - execucao em `live account`

## Decisao de Produto

O produto deixa de ser "um bot generico de opcoes com varias ideias em paralelo" e passa a ser:

- um `short-vol portfolio engine`
- multi-estrategia, mas apenas dentro da familia `defined-risk short vol`
- multi-venue
- sem `backward compatibility`
- com remocao agressiva de codigo legado, scripts redundantes e nomes ambiguos
- com tres modos operacionais:
  - `sim`
  - `paper-broker`
  - `live`

## Escopo Deste Plano

### Em escopo

- unificacao do scanner
- arquitetura unica para `screening -> decisao -> execucao -> monitoramento`
- adaptadores de execucao para `sim`, `paper-broker` e `live`
- descoberta dinamica de universo em todas as venues
- screening inicial de todos os ativos descobertos, sem allowlist hardcoded
- engine de estrategias para:
  - `bull put spread`
  - `bear call spread`
  - `iron condor`
- risk manager de portfolio
- monitoramento em tempo real
- entrada e saida automatica de trades
- persistencia operacional
- backtest e replay mais realistas
- gates de seguranca para paper e live

### Fora de escopo

- estrategias `long premium single-leg`
- butterflies
- calendars/diagonals
- dispersion/correlation trading
- market making

## Resultado Alvo

Ao final da implementacao, o sistema deve permitir um unico comando operacional, por exemplo:

```powershell
python -m options_tradebot.cli.main run-short-vol `
  --config config/defined_risk_short_vol.toml `
  --mode paper-broker `
  --venues mt5,ib
```

Esse comando deve:

1. conectar nas fontes de dados
2. coletar e normalizar chains
3. rodar o screening de oportunidades
4. rankear trades por retorno esperado ajustado a risco e liquidez
5. aplicar limites de portfolio
6. enviar ordens para o adapter correto
7. monitorar fills, risco e condicoes de saida
8. fechar trades quando necessario
9. registrar tudo em logs e estado persistido

## Principios de Arquitetura

### 1. Um unico pipeline operacional

O sistema nao deve ter um script para scan, outro para paper local e outro para live com logica duplicada. Deve existir um pipeline unico com adapters variando por modo.

### 2. Separacao entre estrategia e venue

As estrategias devem operar sobre um modelo normalizado de chain e ordens. A venue nao pode vazar regras para dentro do motor de sinal, exceto em custos, liquidez e regras de exercicio.

### 3. Paper real e live real sao modos de execucao

`paper-broker` e `live` devem usar o mesmo motor de decisao. A unica diferenca deve ser o adapter de execucao e os gates de seguranca.

### 4. Risco vem antes de alpha

Nenhuma oportunidade deve ser enviada sem passar por:

- filtro de liquidez
- filtro de evento
- filtro de portfolio
- limite de exposicao por underlying
- limite de margem e capital em risco
- limite de Greeks agregados

### 5. Portfolio primeiro, trade depois

O ranking final nao deve escolher o "melhor trade isolado". Deve escolher o melhor conjunto incremental para o portfolio.

### 6. Sem compatibilidade retroativa

Nao vale carregar estruturas antigas por conveniencia. O que nao fizer mais parte do desenho alvo deve ser removido, nao adaptado.

### 7. Nada hardcoded no codigo

Parametros operacionais, thresholds, portas, universos opcionais, regras de risco, estrategia e comportamento do runtime devem ficar fora do codigo-fonte.

### 8. Um unico arquivo de configuracao

O sistema deve ter um unico arquivo canonico de configuracao carregado no bootstrap do runtime.

### 9. Nomes e pacotes devem refletir o dominio

Nomes como `service`, `engine`, `main`, `short_vol` e `paper` so devem existir quando forem realmente precisos. Sempre que possivel, o nome do arquivo deve dizer exatamente o que ele faz.

## Configuracao Canonica

### Formato escolhido

Usar `TOML` como formato unico de configuracao, por:

- suporte nativo no Python 3.12 via `tomllib`
- boa legibilidade
- hierarquia clara
- comentarios simples

### Arquivo unico

Criar:

- `config/defined_risk_short_vol.toml`

Esse arquivo deve centralizar absolutamente tudo o que hoje esta disperso em codigo:

- conexoes `MT5`
- conexoes `IB`
- modos operacionais
- parametros de descoberta de universo
- filtros de liquidez
- parametros das estrategias
- limites de portfolio
- comportamento de execucao
- logging
- persistencia
- market hours
- gates de seguranca

### Regra de implementacao

Nenhum threshold, porta, watchlist, peso, limite de risco ou regra de estrategia deve ficar hardcoded no codigo Python. O codigo so le e valida a configuracao.

## Arquitetura Alvo

### Camadas

#### `market_data`

Responsavel por:

- conectar em `MT5` e `IB`
- coletar chains, historico e posicoes
- obter status de conexao e health checks
- descobrir universo dinamicamente em cada venue

Arquivos sugeridos:

- `src/options_tradebot/market_data/collector.py`
- `src/options_tradebot/market_data/models.py`
- `src/options_tradebot/market_data/health_checks.py`

#### `normalization`

Responsavel por:

- transformar dados de `MT5` e `IB` em um schema comum
- limpar quotes cruzadas, mids invalidos, volumes zerados, expiries incoerentes
- padronizar currency, contract multiplier, exercise style, market e venue
- eliminar divergencias entre simbolos de descoberta, simbolos operaveis e simbolos de monitoramento

Arquivos sugeridos:

- `src/options_tradebot/normalization/chains.py`
- `src/options_tradebot/normalization/quotes.py`
- `src/options_tradebot/normalization/contracts.py`

#### `features`

Responsavel por:

- superficie de vol
- skew
- term structure
- realized vol
- forecast vol
- evento
- risco de exercicio antecipado
- custo de execucao
- score de qualidade de descoberta e cobertura de chain

Arquivos sugeridos:

- `src/options_tradebot/features/vol_surface.py`
- `src/options_tradebot/features/liquidity.py`
- `src/options_tradebot/features/events.py`
- `src/options_tradebot/features/assignment.py`
- `src/options_tradebot/features/discovery_quality.py`

#### `strategies`

Responsavel por:

- gerar candidatos
- precificar payoff, edge e risco
- estimar metricas de probabilidade e retorno esperado

Arquivos sugeridos:

- `src/options_tradebot/strategies/defined_risk/types.py`
- `src/options_tradebot/strategies/defined_risk/bull_put_spread.py`
- `src/options_tradebot/strategies/defined_risk/bear_call_spread.py`
- `src/options_tradebot/strategies/defined_risk/iron_condor.py`
- `src/options_tradebot/strategies/defined_risk/trade_selector.py`

#### `portfolio`

Responsavel por:

- limites agregados
- exposicao por underlying
- exposicao por vencimento
- exposicao por estrategia
- Greeks agregados
- margem
- VaR/CVaR
- alocacao de capital

Arquivos sugeridos:

- `src/options_tradebot/portfolio/risk_manager.py`
- `src/options_tradebot/portfolio/sizer.py`
- `src/options_tradebot/portfolio/state.py`

#### `execution`

Responsavel por:

- traduzir sinais em ordens
- suportar `sim`, `paper-broker` e `live`
- reconciliar fills e posicoes
- controlar envio, cancelamento e modificacao

Arquivos sugeridos:

- `src/options_tradebot/execution/order_router.py`
- `src/options_tradebot/execution/modes.py`
- `src/options_tradebot/execution/sim_adapter.py`
- `src/options_tradebot/execution/ib_adapter.py`
- `src/options_tradebot/execution/mt5_adapter.py`

#### `runtime`

Responsavel por:

- loop principal
- agendamento
- health checks
- orchestracao de scan, entrada e saida
- gerenciamento de descoberta e refresh de universo

Arquivos sugeridos:

- `src/options_tradebot/runtime/trading_runtime.py`
- `src/options_tradebot/runtime/position_monitor.py`
- `src/options_tradebot/runtime/universe_refresh.py`

## Estrategias Foco

### 1. Bull Put Spread

Tese:

- vender skew e vol rica em ativos liquidos com vies neutro a levemente altista
- receber credito com perda maxima travada

Regras base:

- vender put OTM com delta alvo
- comprar put mais OTM no mesmo vencimento
- exigir largura maxima por percentual do spot e por risco maximo absoluto
- evitar expiries com evento binario sem premio suficiente

Metricas principais:

- `entry_credit`
- `fair_credit`
- `EV_liquido`
- `return_on_risk`
- `prob_profit`
- `prob_touch`
- `tail_loss_score`
- `liquidity_score`

### 2. Bear Call Spread

Tese:

- vender call OTM em ativos com upside menos provavel, skew assimetrico ou vol rica na asa de calls

Regras base:

- vender call OTM com delta alvo
- comprar call mais OTM no mesmo vencimento
- tratar com cuidado ativos com risco de short squeeze ou gap de noticia

Metricas principais:

- mesmas do bull put spread
- penalidade extra para assimetria de call wing e ex-div quando relevante

### 3. Iron Condor

Tese:

- combinar `bull put spread` e `bear call spread`
- capturar theta e compressao de vol em ativos muito liquidos e regimes mais neutros

Regras base:

- so em underlyings de altissima liquidez
- exigir spreads apertados nos quatro legs
- impedir condors com wings artificiais ou strikes mal distribuidos
- limitar exposicao em eventos

Metricas principais:

- credito total
- perda maxima
- break-even range
- expected move coverage
- EV liquido
- CVaR
- score de simetria e liquidez

## Descoberta de Universo e Screening Dinamico

### Regra principal

O sistema deve comecar descobrindo e varrendo `todos os ativos disponiveis` nas venues conectadas, sem lista inicial hardcoded no codigo.

### Descoberta por venue

#### MT5

- descobrir todos os underlyings com opcoes disponiveis no broker conectado
- mapear expiries, strikes, rights e atributos de negociacao

#### IB

- descobrir o universo optionable configurado para a conta e para o exchange alvo
- construir o universo operavel a partir de descoberta real, e nao de uma watchlist fixa no codigo

### Pipeline de screening

O screening deve ser multi-estagio:

1. `discovery`
- listar todos os ativos e chains disponiveis

2. `coarse filtering`
- remover ativos sem opcoes operaveis
- remover ativos sem chain minima
- remover ativos sem dados confiaveis

3. `liquidity ranking`
- ordenar ativos por qualidade operacional real da chain

4. `strategy-specific candidate generation`
- gerar candidatos por estrategia apenas para ativos que passaram pelos filtros

5. `portfolio-aware final ranking`
- rankear candidatos pela contribuicao incremental ao portfolio

### Observacao importante

Nao ha mais conceito de "universo inicial de ativos liquidos hardcoded". Ha apenas:

- descoberta total
- filtros de qualidade
- ranking dinamico
- veto de risco

### Excecao permitida

O arquivo de configuracao pode permitir restricoes opcionais de universo para:

- testes
- paper controlado
- rollout gradual

Mas a implementacao padrao deve nascer orientada a descoberta total.

## Melhorias no Screening

O score atual deve ser substituido por um ranking mais economico e mais operacional.

### Melhorias estruturais

- separar `asset discovery score` de `trade opportunity score`
- medir cobertura e profundidade da chain antes de tentar montar estruturas
- gerar score especifico por estrategia, e nao um score generico unico para tudo

### Oportunidade deve ser rankeada por

- `expected_value_after_costs`
- `expected_value_per_unit_of_margin`
- `return_on_risk`
- `probability_of_profit`
- `probability_of_touch`
- `probability_of_max_loss_proxy`
- `CVaR_95`
- `liquidity_score`
- `fill_quality_score`
- `event_risk_penalty`
- `portfolio_fit_score`
- `chain_coverage_score`
- `leg_fillability_score`
- `structure_construction_score`

### Modelos recomendados

- superficie de vol com fallback robusto e saneamento forte de quotes
- distribuicao `risk-neutral` para payoff esperado
- overlay de distribuicao `physical` com:
  - realized vol
  - forecast vol
  - jump penalty
  - event regime
- custos explicitos:
  - spread
  - slippage
  - taxas
  - impacto estimado

### Probabilidade de ganho

Nao usar apenas POP "na expiracao". Calcular pelo menos:

- `probability_of_profit_at_expiry`
- `probability_of_touch_before_expiry`
- `probability_of_loss_greater_than_x`
- `expected_pnl_distribution`

### Ranking final

O ranking final deve combinar:

- qualidade da descoberta
- qualidade da chain
- expectativa economica
- liquidez operacional
- ajuste ao portfolio

Nao deve existir um score hardcoded no Python com pesos fixos. Pesos e composicao devem vir do arquivo `TOML`.

## Risco e Alocacao

### Substituir logica de "conta pequena"

Remover o centro da logica atual baseado em:

- `max_contracts` fixo
- `premium_per_contract` como principal limitador

Trocar por:

- `capital_at_risk_limit`
- `margin_usage_limit`
- `net_liq_drawdown_limit`
- `max_loss_per_underlying`
- `max_loss_per_strategy`
- `max_open_positions`
- `max_open_positions_per_expiry`
- `max_short_vega`
- `max_short_gamma`

### Novo risk manager

Implementar:

- risco incremental por trade
- risco agregado por portfolio
- veto por evento
- veto por excesso de correlacao
- veto por baixa liquidez
- veto por risco de exercicio antecipado

## Modos de Execucao

### `sim`

Uso:

- pesquisa
- testes automatizados
- validacao local

Comportamento:

- livro local
- fills sinteticos
- sem envio ao broker

### `paper-broker`

Uso:

- validacao operacional real
- paper trade na corretora

Comportamento:

- ordens reais na conta paper/demo
- leitura de fills e posicoes reais do broker
- reconciliacao contra estado local

### `live`

Uso:

- producao

Comportamento:

- mesmas regras do `paper-broker`
- gates extras de seguranca

## Regras por Venue

### MT5

Planejar o adapter para:

- dados de mercado
- consulta de posicoes
- envio e cancelamento de ordens
- operacao em `demo` como `paper-broker`
- operacao em `live` apenas com gate explicito

Observacao:

- os instrumentos disponiveis dependem do broker e do servidor conectado
- o sistema deve descobrir e usar apenas o universo realmente exposto pelo broker

### Interactive Brokers

Padrao operacional alvo:

- sessao de dados: porta configurada para dados de mercado
- sessao de execucao `paper`: porta da paper account
- sessao de execucao `live`: porta da live account, apenas quando habilitada

O runtime deve suportar:

- dados em uma sessao
- execucao em outra
- reconciliacao cross-session

## Alteracoes Recomendadas no Repositorio

### Arquivos para refatorar

- `src/options_tradebot/config/__init__.py`
- `src/options_tradebot/cli/main.py`
- `src/options_tradebot/scanner/__init__.py`
- `src/options_tradebot/scanner/service.py`
- `src/options_tradebot/execution/__init__.py`
- `src/options_tradebot/connectors/ib.py`
- `src/options_tradebot/data/mt5_client.py`
- `scripts/run_live_market_scan.py`

### Arquivos novos sugeridos

- `config/defined_risk_short_vol.toml`
- `src/options_tradebot/config/loader.py`
- `src/options_tradebot/config/schema.py`
- `src/options_tradebot/strategies/defined_risk/types.py`
- `src/options_tradebot/strategies/defined_risk/bull_put_spread.py`
- `src/options_tradebot/strategies/defined_risk/bear_call_spread.py`
- `src/options_tradebot/strategies/defined_risk/iron_condor.py`
- `src/options_tradebot/strategies/defined_risk/trade_selector.py`
- `src/options_tradebot/portfolio/risk_manager.py`
- `src/options_tradebot/portfolio/allocation_policy.py`
- `src/options_tradebot/execution/order_router.py`
- `src/options_tradebot/execution/sim_adapter.py`
- `src/options_tradebot/execution/ib_adapter.py`
- `src/options_tradebot/execution/mt5_adapter.py`
- `src/options_tradebot/runtime/trading_runtime.py`
- `src/options_tradebot/runtime/position_monitor.py`
- `src/options_tradebot/runtime/startup.py`
- `src/options_tradebot/runtime/universe_refresh.py`
- `src/options_tradebot/normalization/discovery.py`
- `docs/architecture/short_vol_operating_model.md`

### Arquivos para remover

- `src/options_tradebot/config/settings.py`
- `src/options_tradebot/scanner/engine.py`
- `src/options_tradebot/strategies/fair_value.py`
- `src/options_tradebot/strategies/short_vol.py`
- `src/options_tradebot/execution/paper.py`
- `src/options_tradebot/execution/service.py`
- `scripts/run_live_market_scan.py`
- `scripts/backtest_short_vol_mt5.py`
- qualquer script ou modulo legado que nao participe do runtime alvo

## Plano de Implementacao por Fases

## Fase 0 - Alinhamento e Seguranca

### Objetivo

Parar o drift entre documentacao, codigo e modos operacionais.

### Tarefas

- atualizar README e docs para declarar o foco em `defined-risk short vol`
- declarar explicitamente que nao havera `backward compatibility`
- remover ambiguidade entre `paper local` e `paper broker`
- escolher o `TOML` unico de configuracao
- remover parametros operacionais do codigo
- criar flags explicitas:
  - `--mode sim`
  - `--mode paper-broker`
  - `--mode live`
- bloquear qualquer execucao real sem:
  - flag de CLI
  - config habilitada
  - confirmacao de ambiente correto
- definir lista de arquivos e modulos que serao deletados

### Criterio de conclusao

- nenhuma ordem real consegue ser enviada por acidente
- modo de execucao fica explicito em logs e estado
- nenhum parametro operacional critico fica hardcoded no codigo

## Fase 1 - Unificacao do Scanner

### Objetivo

Ter um unico scanner oficial para o sistema.

### Tarefas

- escolher `scanner/service.py` como base ou absorver o melhor de `scanner/engine.py`
- eliminar duplicidade de `MispricingScanner`
- criar uma unica API de scan
- adicionar normalizacao central de quotes
- adicionar descoberta dinamica de universo
- remover qualquer watchlist hardcoded do codigo

### Entregaveis

- um unico modulo de scanner
- testes cobrindo quotes cruzadas, spread negativo e chains incompletas
- descoberta cobrindo todos os ativos disponiveis na venue

### Criterio de conclusao

- todos os entrypoints usam o mesmo scanner

## Fase 2 - Bootstrap, Configuracao e Estrutura Limpa

### Objetivo

Centralizar toda a configuracao e reorganizar os pacotes com nomes claros.

### Tarefas

- criar `config/defined_risk_short_vol.toml`
- criar loader e schema de configuracao
- mover para o `TOML`:
  - conexoes
  - risco
  - estrategia
  - filtros
  - universos opcionais
  - market hours
  - logging
- remover `settings.py`
- renomear e reorganizar modulos para refletir o dominio

### Criterio de conclusao

- o sistema inicia a partir de um unico arquivo de config
- nao existe parametro operacional relevante hardcoded

## Fase 3 - Normalizacao e Saneamento de Dados

### Objetivo

Garantir que `MT5` e `IB` alimentem o mesmo modelo interno.

### Tarefas

- criar normalizador de quotes
- padronizar:
  - `mid`
  - `spread`
  - `spread_pct`
  - `market`
  - `currency`
  - `exercise_style`
  - `contract_multiplier`
- definir regras de descarte de observacoes ruins

### Criterio de conclusao

- nenhum score usa quotes invalidas diretamente

## Fase 4 - Strategy Engine para Bull Put e Bear Call

### Objetivo

Separar e endurecer as duas estrategias verticais simples.

### Tarefas

- extrair a logica de bull put para um modulo proprio
- implementar `bear call spread`
- parametrizar:
  - alvo de delta
  - largura maxima
  - credito minimo
  - score de liquidez
  - regras de evento

### Criterio de conclusao

- ambas as estrategias geram candidatos, score e plano de saida

## Fase 5 - Strategy Engine para Iron Condor

### Objetivo

Montar condors apenas quando houver liquidez suficiente nos quatro legs.

### Tarefas

- gerar asas put e call independentes
- combinar em condors coerentes
- penalizar desequilibrio de asas
- exigir score superior ao de verticais simples quando o custo operacional for maior

### Criterio de conclusao

- condor so aparece quando realmente supera as verticais em retorno ajustado a risco

## Fase 6 - Novo Ranking de Oportunidades

### Objetivo

Parar de rankear por edge bruto e passar a rankear por valor economico.

### Tarefas

- implementar metricas:
  - `EV_liquido`
  - `EV_por_margem`
  - `prob_profit`
  - `prob_touch`
  - `CVaR`
  - `event_penalty`
  - `fill_penalty`
- criar score final por estrategia
- criar score incremental de portfolio

### Criterio de conclusao

- ranking final nao depende apenas de `iv_vs_realized_spread`

## Fase 7 - Portfolio Risk Manager

### Objetivo

Tornar a alocacao escalavel para capital maior.

### Tarefas

- implementar limites por:
  - underlying
  - vencimento
  - estrategia
  - venue
  - margem
  - VaR/CVaR
  - Greeks
- criar alocador incremental
- impedir concentracao excessiva

### Criterio de conclusao

- o sistema pode operar portfolio multi-posicao sem logica de "conta pequena"

## Fase 8 - Execution Router e Reconcilicao

### Objetivo

Ter um unico executor com tres modos.

### Tarefas

- criar `execution/order_router.py`
- criar adapters:
  - `sim`
  - `MT5`
  - `IB`
- implementar reconciliacao de:
  - ordens
  - fills
  - posicoes
  - estado local

### Criterio de conclusao

- o mesmo sinal pode ser executado em qualquer modo sem reescrever a logica

## Fase 9 - Monitoramento de Posicoes e Saidas

### Objetivo

Fechar o ciclo operacional.

### Tarefas

- monitorar posicoes abertas em tempo real
- recalcular risco e PnL continuamente
- acionar saidas por:
  - alvo de captura
  - stop
  - risco de evento
  - proximidade de expiracao
  - assignment risk

### Criterio de conclusao

- entrada e saida ficam dentro do mesmo runtime

## Fase 10 - Backtest e Replay Realistas

### Objetivo

Ter avaliacao mais crivel antes do live.

### Tarefas

- parar de depender de spread sintetico para o caminho principal
- persistir snapshots e books historicos
- criar replay por timestamp
- modelar slippage e fill uncertainty

### Criterio de conclusao

- o backtest fica proximo da operacao real

## Fase 11 - Entry Point Unico

### Objetivo

Substituir scripts isolados por um comando oficial.

### Tarefas

- adicionar comando `run-short-vol`
- suportar selecao de:
  - config
  - modo
  - venues
  - estrategias
- mover a logica central de `scripts/run_live_market_scan.py` para `src`

### Criterio de conclusao

- o operador usa um unico comando para scan, execucao e monitoramento

## Testes

### Unitarios

- payoff e risco das tres estrategias
- score por estrategia
- filtros de liquidez
- assignment risk
- portfolio risk manager

### Integracao

- `MT5` data -> normalizacao -> sinal -> ordem
- `IB` data -> normalizacao -> sinal -> ordem
- dados em uma sessao e execucao em outra no `IB`
- descoberta total -> filtro -> ranking -> ordem

### Replay

- replay de sessao completa
- reabertura de runtime com estado existente
- reconciliacao apos restart

### Paper broker

- paper `MT5 demo`
- paper `IB 4002`

## Operacao e Rollout

### Etapa 1

- `sim` em tempo real
- sem ordens no broker

### Etapa 2

- `paper-broker` em `IB 4002`
- `paper-broker` em `MT5 demo`

### Etapa 3

- live com um unico underlying por venue
- tamanho minimo

### Etapa 4

- ampliar universo
- ampliar numero de posicoes

## Criterios para Ir a Live

- minimo de dias em `paper-broker` sem falha operacional
- slippage media dentro do esperado
- reconciliacao automatica funcionando
- drawdown e CVaR dentro dos limites
- logs e alertas confiaveis

## Ordem Recomendada de Implementacao

1. seguranca de execucao e modos
2. unificacao do scanner
3. configuracao unica e limpeza de legado
4. normalizacao de dados
5. bull put spread
6. bear call spread
7. ranking novo
8. risk manager
9. execution router
10. monitoramento de posicoes
11. iron condor
12. replay realista
13. entrypoint unico

## Definicao de Pronto

O trabalho sera considerado pronto quando:

- houver um unico runtime operacional
- houver um unico arquivo de configuracao
- nao houver legado morto no caminho principal
- `bull put`, `bear call` e `iron condor` estiverem suportados
- `sim`, `paper-broker` e `live` compartilharem o mesmo pipeline
- `MT5` e `IB` estiverem suportados no mesmo modelo
- o sistema comecar descobrindo e varrendo todos os ativos disponiveis
- o sistema abrir e fechar trades automaticamente
- o ranking estiver baseado em risco, retorno e liquidez reais

## Resumo Executivo

O repo deve evoluir de um conjunto de componentes de pesquisa e scripts parcialmente sobrepostos para um `short-vol execution platform` coeso, com foco exclusivo em estrategias de risco definido, descoberta dinamica de universo, configuracao unica em `TOML`, nomes claros, remocao de legado e operacao consistente entre `MT5` e `Interactive Brokers`.
