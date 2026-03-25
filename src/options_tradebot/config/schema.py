"""Canonical short-vol runtime configuration schema."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppConfig:
    mode: str
    venues: tuple[str, ...]
    output_dir: str
    state_file: str
    log_level: str


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    poll_seconds: float
    run_once: bool
    timezone: str
    market_open: str
    market_close: str
    us_market_open: str
    us_market_close: str


@dataclass(frozen=True, slots=True)
class PricingConfig:
    brl_risk_free_rate: float
    usd_risk_free_rate: float
    surface_method: str
    min_surface_points: int
    surface_weight: float
    forecast_weight: float
    minimum_volatility: float
    physical_drift: float
    distribution_grid_size: int


@dataclass(frozen=True, slots=True)
class MT5DiscoveryConfig:
    option_path: str
    dte_min: int
    dte_max: int
    max_expiries_per_underlying: int
    max_strikes_per_right: int
    moneyness_window: float
    selection_wait_seconds: float
    include_underlyings: tuple[str, ...] = ()
    exclude_underlyings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IBDiscoveryConfig:
    symbols: tuple[str, ...]
    exchange: str
    currency: str
    dte_min: int
    dte_max: int
    max_expiries_per_underlying: int
    max_strikes_per_right: int
    moneyness_window: float
    scanner_locations: tuple[str, ...] = ()
    scanner_scan_codes: tuple[str, ...] = ()
    scanner_max_results: int = 0


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    screen_all_discovered_assets: bool
    history_bars: int
    force_exit_dte: int
    event_exit_days: int
    mt5: MT5DiscoveryConfig
    ib: IBDiscoveryConfig


@dataclass(frozen=True, slots=True)
class LiquidityConfig:
    min_short_leg_volume: int
    min_long_leg_volume: int
    min_open_interest: int
    max_short_leg_spread_pct: float
    max_long_leg_spread_pct: float
    max_condor_leg_spread_pct: float


@dataclass(frozen=True, slots=True)
class VerticalStrategyConfig:
    enabled: bool
    short_delta_min: float
    short_delta_max: float
    min_credit: float
    width_pct_of_spot_cap: float
    target_capture_pct: float
    stop_multiple: float
    min_expected_value: float


@dataclass(frozen=True, slots=True)
class IronCondorStrategyConfig:
    enabled: bool
    short_delta_min: float
    short_delta_max: float
    min_total_credit: float
    width_pct_of_spot_cap: float
    target_capture_pct: float
    stop_multiple: float
    min_expected_value: float
    max_total_width_pct_of_spot: float


@dataclass(frozen=True, slots=True)
class StrategiesConfig:
    bull_put: VerticalStrategyConfig
    bear_call: VerticalStrategyConfig
    iron_condor: IronCondorStrategyConfig


@dataclass(frozen=True, slots=True)
class RankingWeightsConfig:
    expected_value: float
    return_on_risk: float
    probability_of_profit: float
    cvar_penalty: float
    liquidity: float
    vol_regime: float


@dataclass(frozen=True, slots=True)
class RankingConfig:
    top_candidates_per_cycle: int
    weights: RankingWeightsConfig


@dataclass(frozen=True, slots=True)
class RiskConfig:
    capital_base: float
    max_total_capital_at_risk_pct: float
    max_position_capital_at_risk_pct: float
    max_positions_total: int
    max_positions_per_underlying: int
    max_positions_per_venue: int
    max_contracts_per_trade: int
    max_abs_delta: float
    max_short_gamma: float
    max_short_vega: float


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    order_type: str
    price_rounding: int
    price_buffer_pct: float
    allow_paper_broker_orders: bool
    allow_live_orders: bool
    mt5_legged_execution_enabled: bool
    tag: str


@dataclass(frozen=True, slots=True)
class MT5EndpointConfig:
    path: str | None
    login: int | None
    password: str | None
    server: str | None
    require_demo: bool = False


@dataclass(frozen=True, slots=True)
class MT5VenueConfig:
    data: MT5EndpointConfig
    paper: MT5EndpointConfig
    live: MT5EndpointConfig


@dataclass(frozen=True, slots=True)
class IBDataEndpointConfig:
    host: str
    port: int
    client_id: int
    account: str | None
    market_data_type: int


@dataclass(frozen=True, slots=True)
class IBExecutionEndpointConfig:
    host: str
    port: int
    client_id: int
    account: str | None


@dataclass(frozen=True, slots=True)
class IBVenueConfig:
    data: IBDataEndpointConfig
    paper: IBExecutionEndpointConfig
    live: IBExecutionEndpointConfig


@dataclass(frozen=True, slots=True)
class VenuesConfig:
    mt5: MT5VenueConfig
    ib: IBVenueConfig


@dataclass(frozen=True, slots=True)
class ShortVolRuntimeConfig:
    path: Path
    app: AppConfig
    runtime: RuntimeConfig
    pricing: PricingConfig
    discovery: DiscoveryConfig
    liquidity: LiquidityConfig
    strategies: StrategiesConfig
    ranking: RankingConfig
    risk: RiskConfig
    execution: ExecutionConfig
    venues: VenuesConfig


def normalize_symbol_list(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not values:
        return ()
    return tuple(sorted({str(value).strip().upper() for value in values if str(value).strip()}))
