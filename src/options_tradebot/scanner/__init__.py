"""Dynamic B3 mispricing scanner."""

from options_tradebot.scanner.models import (
    CrossMarketScanResult,
    CrossMarketVolArbFinding,
    OptionMispricingFinding,
    UnderlyingScanResult,
    cross_market_findings_to_frame,
    scan_results_to_frame,
)
from options_tradebot.scanner.service import MispricingScanner
from options_tradebot.scanner.sources import (
    BrapiListedAsset,
    discover_optionable_assets,
    fetch_all_brapi_tickers,
    fetch_brapi_quote_list,
    load_b3_cotahist,
    scrape_opcoes_net_optionable_assets,
)

__all__ = [
    "BrapiListedAsset",
    "CrossMarketScanResult",
    "CrossMarketVolArbFinding",
    "MispricingScanner",
    "OptionMispricingFinding",
    "UnderlyingScanResult",
    "cross_market_findings_to_frame",
    "discover_optionable_assets",
    "fetch_all_brapi_tickers",
    "fetch_brapi_quote_list",
    "load_b3_cotahist",
    "scan_results_to_frame",
    "scrape_opcoes_net_optionable_assets",
]
