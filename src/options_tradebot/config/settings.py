"""Project configuration objects."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


@dataclass(frozen=True, slots=True)
class UniverseConfig:
    """Universe and microstructure filters for B3 options."""

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
    garch_omega: float = 1e-6
    garch_alpha: float = 0.08
    garch_beta: float = 0.90
    edge_threshold_pct: float = 0.08
    min_edge_to_spread: float = 1.25
    take_profit_pct: float = 0.30
    stop_loss_pct: float = 0.18
    force_exit_dte: int = 2
    fair_vol_surface_weight: float = 0.75
    fair_vol_forecast_weight: float = 0.25
    surface_method: str = "svi"


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
class ScannerConfig:
    """Cross-sectional scanner configuration."""

    top_n_underlyings: int = 10
    top_option_count: int = 5
    trade_top_n: int = 5
    realized_vol_lookback: int = 20
    atm_target_dte: int = 14
    iv_history_lookback: int = 60
    event_window_days: int = 10
    refresh_minutes: int = 30
    market_open: str = "10:00"
    market_close: str = "16:55"
    event_decay_days: float = 10.0
    max_tradeable_spread_pct: float = 0.18
    min_surface_points: int = 5
    surface_method: str = "svi"
    use_corrado_su_adjustment: bool = True
    weight_iv_spread: float = 0.25
    weight_iv_rank: float = 0.15
    weight_skew: float = 0.15
    weight_flow: float = 0.15
    weight_liquidity: float = 0.15
    weight_event: float = 0.05
    weight_option_edge: float = 0.10
    weight_skew_alpha: float = 0.15
    skew_alpha_underlyings: tuple[str, ...] = ("PETR4", "VALE3")


@dataclass(frozen=True, slots=True)
class EnvironmentConfig:
    """Runtime defaults loaded from environment variables."""

    mt5_login: int | None = None
    mt5_password: str | None = None
    mt5_server: str | None = None
    mt5_path: str | None = None
    mode: str = "paper"


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Bundled project settings."""

    universe: UniverseConfig = field(default_factory=UniverseConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    paper: PaperTradingConfig = field(default_factory=PaperTradingConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)
    default_risk_free_rate: float = 0.14


def default_settings() -> AppSettings:
    """Return the default app settings."""

    _load_dotenv()
    paper = PaperTradingConfig(
        output_dir=os.environ.get("OPTIONS_TRADEBOT_OUTPUT_DIR", PaperTradingConfig.output_dir),
    )
    environment = EnvironmentConfig(
        mt5_login=_env_int("MT5_LOGIN"),
        mt5_password=os.environ.get("MT5_PASSWORD") or None,
        mt5_server=os.environ.get("MT5_SERVER") or None,
        mt5_path=os.environ.get("MT5_PATH") or None,
        mode=os.environ.get("OPTIONS_TRADEBOT_MODE", EnvironmentConfig.mode),
    )
    return AppSettings(paper=paper, environment=environment)


def _env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return None
    return int(value)


def _load_dotenv(path: str = ".env") -> None:
    dotenv = Path(path)
    if not dotenv.exists():
        return
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
