"""Venue-aware market data collection for the short-vol runtime."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from options_tradebot.config.schema import ShortVolRuntimeConfig
from options_tradebot.connectors.ib import IBGatewayClient, IBGatewayConfig
from options_tradebot.data.mt5_client import MT5ConnectionConfig, MT5MarketDataClient
from options_tradebot.market.models import OptionSnapshot


@dataclass(frozen=True, slots=True)
class CollectedVenueData:
    venue: str
    snapshots: tuple[OptionSnapshot, ...]
    histories: dict[str, pd.Series]
    discovered_underlyings: tuple[str, ...]
    issues: tuple[str, ...] = ()


def collect_market_data(
    *,
    config: ShortVolRuntimeConfig,
    venues: tuple[str, ...] | None = None,
) -> list[CollectedVenueData]:
    selected_venues = venues or config.app.venues
    collected: list[CollectedVenueData] = []
    for venue in selected_venues:
        normalized = venue.strip().lower()
        if normalized == "mt5":
            collected.append(_collect_mt5_market_data(config))
        elif normalized == "ib":
            collected.append(_collect_ib_market_data(config))
    return collected


def _collect_mt5_market_data(config: ShortVolRuntimeConfig) -> CollectedVenueData:
    endpoint = config.venues.mt5.data
    client = MT5MarketDataClient(
        MT5ConnectionConfig(
            path=endpoint.path,
            login=endpoint.login,
            password=endpoint.password,
            server=endpoint.server,
        )
    )
    histories: dict[str, pd.Series] = {}
    snapshots: list[OptionSnapshot] = []
    issues: list[str] = []
    discovered: list[str] = []
    try:
        client.connect()
        discovered = client.available_option_underlyings(option_path=config.discovery.mt5.option_path)
        if config.discovery.mt5.include_underlyings:
            allowed = set(config.discovery.mt5.include_underlyings)
            discovered = [value for value in discovered if value in allowed]
        if config.discovery.mt5.exclude_underlyings:
            blocked = set(config.discovery.mt5.exclude_underlyings)
            discovered = [value for value in discovered if value not in blocked]
        snapshots = client.collect_live_option_snapshots(
            underlyings=discovered or None,
            dte_min=config.discovery.mt5.dte_min,
            dte_max=config.discovery.mt5.dte_max,
            max_expiries_per_underlying=config.discovery.mt5.max_expiries_per_underlying,
            max_strikes_per_right=config.discovery.mt5.max_strikes_per_right,
            moneyness_window=config.discovery.mt5.moneyness_window,
            risk_free_rate=config.pricing.brl_risk_free_rate,
            option_path=config.discovery.mt5.option_path,
            selection_wait_seconds=config.discovery.mt5.selection_wait_seconds,
        )
        for underlying in discovered:
            try:
                bars = client.fetch_bars(symbol=underlying, timeframe="D1", count=config.discovery.history_bars)
            except Exception as error:
                issues.append(f"mt5_history:{underlying}:{error}")
                continue
            if "Close" in bars:
                histories[underlying] = bars["Close"].astype(float)
    except Exception as error:
        issues.append(f"mt5_collect:{error}")
    finally:
        client.shutdown()
    return CollectedVenueData(
        venue="mt5",
        snapshots=tuple(snapshots),
        histories=histories,
        discovered_underlyings=tuple(discovered),
        issues=tuple(issues),
    )


def _collect_ib_market_data(config: ShortVolRuntimeConfig) -> CollectedVenueData:
    endpoint = config.venues.ib.data
    client = IBGatewayClient(
        IBGatewayConfig(
            host=endpoint.host,
            data_port=endpoint.port,
            data_client_id=endpoint.client_id,
            execution_port=config.venues.ib.paper.port,
            execution_client_id=config.venues.ib.paper.client_id,
            account=endpoint.account,
            market_data_type=endpoint.market_data_type,
            risk_free_rate=config.pricing.usd_risk_free_rate,
            read_only_data_only=True,
        )
    )
    histories: dict[str, pd.Series] = {}
    snapshots: list[OptionSnapshot] = []
    issues: list[str] = []
    symbols = list(config.discovery.ib.symbols)
    try:
        client.connect()
        if config.discovery.screen_all_discovered_assets or not symbols:
            try:
                symbols = sorted(
                    dict.fromkeys(
                        [
                            *symbols,
                            *client.discover_optionable_underlyings(
                                locations=config.discovery.ib.scanner_locations,
                                scan_codes=config.discovery.ib.scanner_scan_codes,
                                max_results=config.discovery.ib.scanner_max_results,
                            ),
                        ]
                    )
                )
            except Exception as error:
                issues.append(f"ib_discovery:{error}")
        for symbol in symbols:
            try:
                symbol_snapshots = client.fetch_option_snapshots(
                    symbol,
                    exchange=config.discovery.ib.exchange,
                    currency=config.discovery.ib.currency,
                    max_expiries=config.discovery.ib.max_expiries_per_underlying,
                    max_strikes=config.discovery.ib.max_strikes_per_right,
                    moneyness_window=config.discovery.ib.moneyness_window,
                )
                if not symbol_snapshots:
                    symbol_snapshots = client.fetch_option_history_snapshots(
                        symbol,
                        exchange=config.discovery.ib.exchange,
                        currency=config.discovery.ib.currency,
                        max_expiries=config.discovery.ib.max_expiries_per_underlying,
                        max_strikes=config.discovery.ib.max_strikes_per_right,
                        moneyness_window=config.discovery.ib.moneyness_window,
                        duration_str="5 D",
                        bar_size="1 day",
                    )
                snapshots.extend(symbol_snapshots)
                bars = client.fetch_underlying_history(
                    symbol,
                    exchange=config.discovery.ib.exchange,
                    currency=config.discovery.ib.currency,
                    duration_str=f"{config.discovery.history_bars} D",
                )
                if not bars.empty and "close" in bars:
                    histories[symbol] = bars["close"].astype(float)
            except Exception as error:
                issues.append(f"ib_symbol:{symbol}:{error}")
    except Exception as error:
        issues.append(f"ib_collect:{error}")
    finally:
        client.disconnect()
    if not symbols:
        issues.append("ib_collect:no_symbols_discovered")
    return CollectedVenueData(
        venue="ib",
        snapshots=tuple(snapshots),
        histories=histories,
        discovered_underlyings=tuple(symbols),
        issues=tuple(issues),
    )
