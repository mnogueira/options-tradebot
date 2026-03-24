"""Project configuration objects."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class UniverseConfig:
    """Universe and microstructure filters for B3 options."""

    primary_underlyings: tuple[str, ...] = ("BOVA11", "PETR4", "VALE3")
    secondary_underlyings: tuple[str, ...] = ("WDO",)
    min_open_interest: int = 5_000
    min_daily_volume: int = 1_000
    min_dte: int = 5
    max_dte: int = 25
    min_delta: float = 0.25
    max_delta: float = 0.55
    max_spread_pct: float = 0.12
    max_premium_per_contract: float = 1_500.0
    min_premium_per_contract: float = 50.0


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """Signal generation configuration."""

    fast_ema: int = 5
    slow_ema: int = 21
    realized_vol_lookback: int = 20
    garch_alpha: float = 0.08
    garch_beta: float = 0.90
    edge_threshold_pct: float = 0.08
    min_edge_to_spread: float = 1.25
    take_profit_pct: float = 0.30
    stop_loss_pct: float = 0.18
    force_exit_dte: int = 2
    fair_vol_surface_weight: float = 0.75
    fair_vol_forecast_weight: float = 0.25


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """Position sizing and portfolio risk limits."""

    risk_per_trade_pct: float = 0.02
    max_contracts: int = 5
    max_abs_delta: float = 250.0
    max_abs_gamma: float = 15.0
    max_abs_vega: float = 500.0
    max_portfolio_drawdown_pct: float = 0.12
    max_daily_loss_pct: float = 0.03


@dataclass(frozen=True, slots=True)
class PaperTradingConfig:
    """Paper-trading service parameters."""

    initial_cash: float = 10_000.0
    output_dir: str = "runtime/paper"
    poll_seconds: int = 15
    allow_live_orders: bool = False
    allow_same_underlying_overlap: bool = False


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Bundled project settings."""

    universe: UniverseConfig = field(default_factory=UniverseConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    paper: PaperTradingConfig = field(default_factory=PaperTradingConfig)
    default_risk_free_rate: float = 0.14


def default_settings() -> AppSettings:
    """Return the default app settings."""

    return AppSettings()
