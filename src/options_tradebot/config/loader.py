"""Load the canonical short-vol runtime configuration from TOML."""

from __future__ import annotations

from pathlib import Path
import tomllib

from options_tradebot.config.schema import (
    AppConfig,
    DiscoveryConfig,
    ExecutionConfig,
    IBDataEndpointConfig,
    IBDiscoveryConfig,
    IBExecutionEndpointConfig,
    IBVenueConfig,
    IronCondorStrategyConfig,
    LiquidityConfig,
    MT5DiscoveryConfig,
    MT5EndpointConfig,
    MT5VenueConfig,
    PricingConfig,
    RankingConfig,
    RankingWeightsConfig,
    RiskConfig,
    RuntimeConfig,
    ShortVolRuntimeConfig,
    StrategiesConfig,
    VenuesConfig,
    VerticalStrategyConfig,
    normalize_symbol_list,
)


def load_short_vol_config(path: str | Path = "config/defined_risk_short_vol.toml") -> ShortVolRuntimeConfig:
    config_path = Path(path).resolve()
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)

    app_section = payload["app"]
    runtime_section = payload["runtime"]
    pricing_section = payload["pricing"]
    discovery_section = payload["discovery"]
    discovery_mt5_section = discovery_section["mt5"]
    discovery_ib_section = discovery_section["ib"]
    liquidity_section = payload["liquidity"]
    strategy_section = payload["strategies"]
    ranking_section = payload["ranking"]
    ranking_weights_section = ranking_section["weights"]
    risk_section = payload["risk"]
    execution_section = payload["execution"]
    venues_section = payload["venues"]
    mt5_section = venues_section["mt5"]
    ib_section = venues_section["ib"]

    return ShortVolRuntimeConfig(
        path=config_path,
        app=AppConfig(
            mode=str(app_section["mode"]).strip().lower(),
            venues=tuple(str(value).strip().lower() for value in app_section["venues"]),
            output_dir=str(app_section["output_dir"]),
            state_file=str(app_section["state_file"]),
            log_level=str(app_section["log_level"]).upper(),
        ),
        runtime=RuntimeConfig(
            poll_seconds=float(runtime_section["poll_seconds"]),
            run_once=bool(runtime_section["run_once"]),
            timezone=str(runtime_section["timezone"]),
            market_open=str(runtime_section["market_open"]),
            market_close=str(runtime_section["market_close"]),
            us_market_open=str(runtime_section["us_market_open"]),
            us_market_close=str(runtime_section["us_market_close"]),
        ),
        pricing=PricingConfig(
            brl_risk_free_rate=float(pricing_section["brl_risk_free_rate"]),
            usd_risk_free_rate=float(pricing_section["usd_risk_free_rate"]),
            surface_method=str(pricing_section["surface_method"]).lower(),
            min_surface_points=int(pricing_section["min_surface_points"]),
            surface_weight=float(pricing_section["surface_weight"]),
            forecast_weight=float(pricing_section["forecast_weight"]),
            minimum_volatility=float(pricing_section["minimum_volatility"]),
            physical_drift=float(pricing_section["physical_drift"]),
            distribution_grid_size=int(pricing_section["distribution_grid_size"]),
        ),
        discovery=DiscoveryConfig(
            screen_all_discovered_assets=bool(discovery_section["screen_all_discovered_assets"]),
            history_bars=int(discovery_section["history_bars"]),
            force_exit_dte=int(discovery_section["force_exit_dte"]),
            event_exit_days=int(discovery_section["event_exit_days"]),
            mt5=MT5DiscoveryConfig(
                option_path=str(discovery_mt5_section["option_path"]),
                dte_min=int(discovery_mt5_section["dte_min"]),
                dte_max=int(discovery_mt5_section["dte_max"]),
                max_expiries_per_underlying=int(discovery_mt5_section["max_expiries_per_underlying"]),
                max_strikes_per_right=int(discovery_mt5_section["max_strikes_per_right"]),
                moneyness_window=float(discovery_mt5_section["moneyness_window"]),
                selection_wait_seconds=float(discovery_mt5_section["selection_wait_seconds"]),
                include_underlyings=normalize_symbol_list(discovery_mt5_section.get("include_underlyings")),
                exclude_underlyings=normalize_symbol_list(discovery_mt5_section.get("exclude_underlyings")),
            ),
            ib=IBDiscoveryConfig(
                symbols=normalize_symbol_list(discovery_ib_section.get("symbols")),
                exchange=str(discovery_ib_section.get("exchange", "SMART")).strip().upper(),
                currency=str(discovery_ib_section.get("currency", "USD")).strip().upper(),
                dte_min=int(discovery_ib_section["dte_min"]),
                dte_max=int(discovery_ib_section["dte_max"]),
                max_expiries_per_underlying=int(discovery_ib_section["max_expiries_per_underlying"]),
                max_strikes_per_right=int(discovery_ib_section["max_strikes_per_right"]),
                moneyness_window=float(discovery_ib_section["moneyness_window"]),
                scanner_locations=tuple(
                    str(value).strip().upper()
                    for value in discovery_ib_section.get("scanner_locations", [])
                    if str(value).strip()
                ),
                scanner_scan_codes=tuple(
                    str(value).strip().upper()
                    for value in discovery_ib_section.get("scanner_scan_codes", [])
                    if str(value).strip()
                ),
                scanner_max_results=int(discovery_ib_section.get("scanner_max_results", 0)),
            ),
        ),
        liquidity=LiquidityConfig(
            min_short_leg_volume=int(liquidity_section["min_short_leg_volume"]),
            min_long_leg_volume=int(liquidity_section["min_long_leg_volume"]),
            min_open_interest=int(liquidity_section["min_open_interest"]),
            max_short_leg_spread_pct=float(liquidity_section["max_short_leg_spread_pct"]),
            max_long_leg_spread_pct=float(liquidity_section["max_long_leg_spread_pct"]),
            max_condor_leg_spread_pct=float(liquidity_section["max_condor_leg_spread_pct"]),
        ),
        strategies=StrategiesConfig(
            bull_put=_load_vertical_strategy(strategy_section["bull_put"]),
            bear_call=_load_vertical_strategy(strategy_section["bear_call"]),
            iron_condor=IronCondorStrategyConfig(
                enabled=bool(strategy_section["iron_condor"]["enabled"]),
                short_delta_min=float(strategy_section["iron_condor"]["short_delta_min"]),
                short_delta_max=float(strategy_section["iron_condor"]["short_delta_max"]),
                min_total_credit=float(strategy_section["iron_condor"]["min_total_credit"]),
                width_pct_of_spot_cap=float(strategy_section["iron_condor"]["width_pct_of_spot_cap"]),
                target_capture_pct=float(strategy_section["iron_condor"]["target_capture_pct"]),
                stop_multiple=float(strategy_section["iron_condor"]["stop_multiple"]),
                min_expected_value=float(strategy_section["iron_condor"]["min_expected_value"]),
                max_total_width_pct_of_spot=float(strategy_section["iron_condor"]["max_total_width_pct_of_spot"]),
            ),
        ),
        ranking=RankingConfig(
            top_candidates_per_cycle=int(ranking_section["top_candidates_per_cycle"]),
            weights=RankingWeightsConfig(
                expected_value=float(ranking_weights_section["expected_value"]),
                return_on_risk=float(ranking_weights_section["return_on_risk"]),
                probability_of_profit=float(ranking_weights_section["probability_of_profit"]),
                cvar_penalty=float(ranking_weights_section["cvar_penalty"]),
                liquidity=float(ranking_weights_section["liquidity"]),
                vol_regime=float(ranking_weights_section["vol_regime"]),
            ),
        ),
        risk=RiskConfig(
            capital_base=float(risk_section["capital_base"]),
            max_total_capital_at_risk_pct=float(risk_section["max_total_capital_at_risk_pct"]),
            max_position_capital_at_risk_pct=float(risk_section["max_position_capital_at_risk_pct"]),
            max_positions_total=int(risk_section["max_positions_total"]),
            max_positions_per_underlying=int(risk_section["max_positions_per_underlying"]),
            max_positions_per_venue=int(risk_section["max_positions_per_venue"]),
            max_contracts_per_trade=int(risk_section["max_contracts_per_trade"]),
            max_abs_delta=float(risk_section["max_abs_delta"]),
            max_short_gamma=float(risk_section["max_short_gamma"]),
            max_short_vega=float(risk_section["max_short_vega"]),
        ),
        execution=ExecutionConfig(
            order_type=str(execution_section["order_type"]).upper(),
            price_rounding=int(execution_section["price_rounding"]),
            price_buffer_pct=float(execution_section["price_buffer_pct"]),
            allow_paper_broker_orders=bool(execution_section["allow_paper_broker_orders"]),
            allow_live_orders=bool(execution_section["allow_live_orders"]),
            mt5_legged_execution_enabled=bool(execution_section["mt5_legged_execution_enabled"]),
            tag=str(execution_section["tag"]),
        ),
        venues=VenuesConfig(
            mt5=MT5VenueConfig(
                data=_load_mt5_endpoint(mt5_section["data"], require_demo=False),
                paper=_load_mt5_endpoint(mt5_section["paper"], require_demo=bool(mt5_section["paper"]["require_demo"])),
                live=_load_mt5_endpoint(mt5_section["live"], require_demo=bool(mt5_section["live"]["require_demo"])),
            ),
            ib=IBVenueConfig(
                data=IBDataEndpointConfig(
                    host=str(ib_section["data"]["host"]),
                    port=int(ib_section["data"]["port"]),
                    client_id=int(ib_section["data"]["client_id"]),
                    account=_optional_str(ib_section["data"].get("account")),
                    market_data_type=int(ib_section["data"]["market_data_type"]),
                ),
                paper=IBExecutionEndpointConfig(
                    host=str(ib_section["paper"]["host"]),
                    port=int(ib_section["paper"]["port"]),
                    client_id=int(ib_section["paper"]["client_id"]),
                    account=_optional_str(ib_section["paper"].get("account")),
                ),
                live=IBExecutionEndpointConfig(
                    host=str(ib_section["live"]["host"]),
                    port=int(ib_section["live"]["port"]),
                    client_id=int(ib_section["live"]["client_id"]),
                    account=_optional_str(ib_section["live"].get("account")),
                ),
            ),
        ),
    )


def _load_vertical_strategy(payload: dict[str, object]) -> VerticalStrategyConfig:
    return VerticalStrategyConfig(
        enabled=bool(payload["enabled"]),
        short_delta_min=float(payload["short_delta_min"]),
        short_delta_max=float(payload["short_delta_max"]),
        min_credit=float(payload["min_credit"]),
        width_pct_of_spot_cap=float(payload["width_pct_of_spot_cap"]),
        target_capture_pct=float(payload["target_capture_pct"]),
        stop_multiple=float(payload["stop_multiple"]),
        min_expected_value=float(payload["min_expected_value"]),
    )


def _load_mt5_endpoint(payload: dict[str, object], *, require_demo: bool) -> MT5EndpointConfig:
    login = int(payload["login"]) if int(payload["login"]) > 0 else None
    return MT5EndpointConfig(
        path=_optional_str(payload.get("path")),
        login=login,
        password=_optional_str(payload.get("password")),
        server=_optional_str(payload.get("server")),
        require_demo=require_demo,
    )


def _optional_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
