"""Interactive Brokers Gateway connector built on top of ib_async."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from math import exp, log
from typing import Any, Callable, Iterable, Sequence

import pandas as pd

from options_tradebot.market.models import GreekVector, OptionContract, OptionKind, OptionQuote, OptionSnapshot
from options_tradebot.market.pricing import implied_volatility


@dataclass(frozen=True, slots=True)
class IBGatewayConfig:
    """Connection and market-data settings for IB Gateway."""

    host: str = "127.0.0.1"
    port: int = 4002
    client_id: int = 3
    timeout: float = 4.0
    market_data_type: int = 1
    account: str | None = None
    option_exchange: str = "SMART"
    quote_wait_seconds: float = 1.0
    order_wait_seconds: float = 1.0
    qualify_chunk_size: int = 40
    risk_free_rate: float = 0.045
    dividend_yield: float = 0.0
    generic_ticks: str = "100,101,104,106,221,233"


@dataclass(frozen=True, slots=True)
class IBOptionChain:
    """A discoverable IB option chain for one underlying."""

    underlying: str
    exchange: str
    trading_class: str
    multiplier: int
    expirations: tuple[date, ...]
    strikes: tuple[float, ...]
    underlying_contract_id: int


@dataclass(frozen=True, slots=True)
class IBOptionMarketDataSubscription:
    """Active IB market-data handles for a set of option contracts."""

    underlying_symbol: str
    underlying_price: float
    contracts: tuple[Any, ...]
    tickers: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class IBOrderRequest:
    """Single-leg or combo order request."""

    action: str
    quantity: float
    order_type: str = "LMT"
    limit_price: float | None = None
    tif: str = "DAY"
    account: str | None = None
    order_ref: str | None = None
    transmit: bool = True


@dataclass(frozen=True, slots=True)
class IBSpreadLeg:
    """One spread leg for an IB combo order."""

    contract: OptionContract | OptionSnapshot
    action: str
    ratio: int = 1
    exchange: str = "SMART"


@dataclass(frozen=True, slots=True)
class IBOrderReceipt:
    """High-level order placement result."""

    order_id: int | None
    perm_id: int | None
    symbol: str
    action: str
    order_type: str
    status: str
    filled: float
    remaining: float
    avg_fill_price: float


@dataclass(frozen=True, slots=True)
class IBPositionSnapshot:
    """Current IB option position with live Greeks and PnL metadata."""

    account: str
    symbol: str
    underlying: str
    quantity: float
    avg_cost: float
    market_price: float | None
    market_value: float | None
    unrealized_pnl: float | None
    realized_pnl: float | None
    greeks: GreekVector | None
    snapshot: OptionSnapshot | None


class IBGatewayClient:
    """Synchronous IB Gateway helper built on ib_async's blocking interface."""

    def __init__(
        self,
        config: IBGatewayConfig | None = None,
        *,
        ib_factory: Callable[[], Any] | None = None,
    ):
        self.config = config or IBGatewayConfig()
        self._ib_factory = ib_factory or self._default_ib_factory
        self._ib: Any | None = None
        self._connected = False

    def connect(self) -> dict[str, object]:
        """Connect to IB Gateway and return account/session metadata."""

        ib = self._ib_factory()
        ib.connect(
            self.config.host,
            self.config.port,
            clientId=self.config.client_id,
            timeout=self.config.timeout,
        )
        self._ib = ib
        self._connected = True
        if hasattr(ib, "reqMarketDataType"):
            ib.reqMarketDataType(self.config.market_data_type)
        return self.account_snapshot()

    def disconnect(self) -> None:
        """Disconnect from IB Gateway."""

        if self._ib is not None and hasattr(self._ib, "disconnect"):
            self._ib.disconnect()
        self._connected = False
        self._ib = None

    shutdown = disconnect

    def account_snapshot(self) -> dict[str, object]:
        """Return a lightweight account summary for diagnostics."""

        ib = self._require_connected()
        managed_accounts = list(_safe_call(ib, "managedAccounts", default=[]))
        return {
            "host": self.config.host,
            "port": self.config.port,
            "client_id": self.config.client_id,
            "market_data_type": self.config.market_data_type,
            "managed_accounts": managed_accounts,
            "configured_account": self.config.account,
            "connected": bool(_safe_call(ib, "isConnected", default=True)),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    def qualify_equity(
        self,
        symbol: str,
        *,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> Any:
        """Qualify a US stock contract for options discovery."""

        runtime = _load_ib_async()
        contract = runtime["Stock"](symbol, exchange, currency)
        return self._qualify_contract(contract)

    def discover_option_chain(
        self,
        symbol: str,
        *,
        exchange: str = "SMART",
        currency: str = "USD",
        trading_class: str | None = None,
    ) -> list[IBOptionChain]:
        """Return all expiries and strikes available for a US optionable stock."""

        ib = self._require_connected()
        underlying = self.qualify_equity(symbol, exchange=exchange, currency=currency)
        chains = ib.reqSecDefOptParams(
            symbol,
            "",
            getattr(underlying, "secType", "STK"),
            getattr(underlying, "conId"),
        )
        option_chains: list[IBOptionChain] = []
        for chain in chains or []:
            chain_exchange = str(getattr(chain, "exchange", "") or exchange)
            chain_trading_class = str(getattr(chain, "tradingClass", "") or symbol)
            if trading_class and chain_trading_class != trading_class:
                continue
            option_chains.append(
                IBOptionChain(
                    underlying=symbol,
                    exchange=chain_exchange,
                    trading_class=chain_trading_class,
                    multiplier=int(float(getattr(chain, "multiplier", 100) or 100)),
                    expirations=tuple(sorted(_parse_ib_expiry(value) for value in getattr(chain, "expirations", ()))),
                    strikes=tuple(sorted(float(value) for value in getattr(chain, "strikes", ()))),
                    underlying_contract_id=int(getattr(underlying, "conId")),
                )
            )
        return option_chains

    def build_option_contracts(
        self,
        symbol: str,
        *,
        exchange: str = "SMART",
        currency: str = "USD",
        trading_class: str | None = None,
        expirations: Sequence[date] | None = None,
        strikes: Sequence[float] | None = None,
        option_types: Sequence[OptionKind | str] | None = None,
        max_expiries: int | None = None,
        max_strikes: int | None = None,
        max_contracts: int | None = None,
        moneyness_window: float | None = None,
        underlying_price: float | None = None,
    ) -> list[Any]:
        """Build and qualify IB option contracts for a symbol."""

        runtime = _load_ib_async()
        chains = self.discover_option_chain(
            symbol,
            exchange=exchange,
            currency=currency,
            trading_class=trading_class,
        )
        if not chains:
            return []
        selected_chain = self._select_chain(
            chains,
            preferred_exchange=exchange,
            preferred_trading_class=trading_class,
        )
        underlying = self.qualify_equity(symbol, exchange=exchange, currency=currency)
        resolved_underlying_price = (
            float(underlying_price)
            if underlying_price is not None and underlying_price > 0
            else self._snapshot_underlying_price(underlying)
        )
        selected_expirations = list(expirations or selected_chain.expirations)
        if max_expiries is not None:
            selected_expirations = selected_expirations[:max_expiries]
        selected_strikes = list(strikes or selected_chain.strikes)
        if moneyness_window is not None and resolved_underlying_price > 0:
            selected_strikes = [
                value
                for value in selected_strikes
                if abs(log(max(float(value), 1e-8) / max(resolved_underlying_price, 1e-8))) <= moneyness_window
            ]
        if max_strikes is not None and resolved_underlying_price > 0 and len(selected_strikes) > max_strikes:
            selected_strikes = _nearest_strikes(selected_strikes, reference=resolved_underlying_price, limit=max_strikes)
        normalized_option_types = _normalize_option_types(option_types)
        contracts: list[Any] = []
        for expiry in selected_expirations:
            expiry_code = pd.Timestamp(expiry).strftime("%Y%m%d")
            for strike in selected_strikes:
                for option_type in normalized_option_types:
                    contracts.append(
                        runtime["Option"](
                            symbol,
                            expiry_code,
                            float(strike),
                            "C" if option_type == OptionKind.CALL else "P",
                            selected_chain.exchange or self.config.option_exchange,
                            str(selected_chain.multiplier),
                            currency,
                            tradingClass=selected_chain.trading_class,
                        )
                    )
        if max_contracts is not None:
            contracts = contracts[:max_contracts]
        qualified: list[Any] = []
        for chunk in _chunked(contracts, self.config.qualify_chunk_size):
            result = self._require_connected().qualifyContracts(*chunk)
            if result:
                qualified.extend(
                    contract
                    for contract in result
                    if contract is not None and getattr(contract, "secType", None) is not None
                )
        return qualified

    def fetch_option_snapshots(
        self,
        symbol: str,
        *,
        exchange: str = "SMART",
        currency: str = "USD",
        trading_class: str | None = None,
        expirations: Sequence[date] | None = None,
        strikes: Sequence[float] | None = None,
        option_types: Sequence[OptionKind | str] | None = None,
        max_expiries: int | None = None,
        max_strikes: int | None = None,
        max_contracts: int | None = None,
        moneyness_window: float | None = 0.20,
        wait_seconds: float | None = None,
    ) -> list[OptionSnapshot]:
        """Fetch snapshot option quotes with IB-provided Greeks."""

        subscription = self.subscribe_option_market_data(
            symbol,
            exchange=exchange,
            currency=currency,
            trading_class=trading_class,
            expirations=expirations,
            strikes=strikes,
            option_types=option_types,
            max_expiries=max_expiries,
            max_strikes=max_strikes,
            max_contracts=max_contracts,
            moneyness_window=moneyness_window,
            wait_seconds=wait_seconds,
            snapshot=True,
        )
        return self.subscription_snapshots(subscription)

    def subscribe_option_market_data(
        self,
        symbol: str,
        *,
        exchange: str = "SMART",
        currency: str = "USD",
        trading_class: str | None = None,
        expirations: Sequence[date] | None = None,
        strikes: Sequence[float] | None = None,
        option_types: Sequence[OptionKind | str] | None = None,
        max_expiries: int | None = None,
        max_strikes: int | None = None,
        max_contracts: int | None = None,
        moneyness_window: float | None = 0.20,
        wait_seconds: float | None = None,
        snapshot: bool = False,
    ) -> IBOptionMarketDataSubscription:
        """Subscribe to IB option market data and return live ticker handles."""

        ib = self._require_connected()
        underlying = self.qualify_equity(symbol, exchange=exchange, currency=currency)
        underlying_price = self._snapshot_underlying_price(underlying)
        contracts = self.build_option_contracts(
            symbol,
            exchange=exchange,
            currency=currency,
            trading_class=trading_class,
            expirations=expirations,
            strikes=strikes,
            option_types=option_types,
            max_expiries=max_expiries,
            max_strikes=max_strikes,
            max_contracts=max_contracts,
            moneyness_window=moneyness_window,
        )
        tickers = tuple(
            ib.reqMktData(
                contract,
                genericTickList=self.config.generic_ticks,
                snapshot=snapshot,
                regulatorySnapshot=False,
                mktDataOptions=[],
            )
            for contract in contracts
        )
        ib.sleep(wait_seconds or self.config.quote_wait_seconds)
        return IBOptionMarketDataSubscription(
            underlying_symbol=symbol,
            underlying_price=underlying_price,
            contracts=tuple(contracts),
            tickers=tickers,
        )

    def subscription_snapshots(
        self,
        subscription: IBOptionMarketDataSubscription,
    ) -> list[OptionSnapshot]:
        """Translate live ticker handles into the project's shared snapshot model."""

        snapshots: list[OptionSnapshot] = []
        for contract, ticker in zip(subscription.contracts, subscription.tickers):
            snapshot = self._option_snapshot_from_market_data(
                contract=contract,
                ticker=ticker,
                fallback_underlying_price=subscription.underlying_price,
            )
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    def cancel_market_data(self, subscription: IBOptionMarketDataSubscription) -> None:
        """Cancel an active market-data subscription."""

        ib = self._require_connected()
        for contract in subscription.contracts:
            ib.cancelMktData(contract)

    def fetch_option_history(
        self,
        contract: OptionContract | OptionSnapshot,
        *,
        duration_str: str = "30 D",
        bar_size: str = "1 day",
        what_to_show: str = "TRADES",
        use_rth: bool = True,
        keep_up_to_date: bool = False,
    ) -> pd.DataFrame:
        """Fetch historical data for a qualified option contract."""

        ib = self._require_connected()
        ib_contract = self._resolve_option_contract(contract)
        bars = ib.reqHistoricalData(
            ib_contract,
            endDateTime="",
            durationStr=duration_str,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=2,
            keepUpToDate=keep_up_to_date,
            chartOptions=[],
            timeout=max(self.config.timeout, 1.0) * 10.0,
        )
        rows = [
            {
                "date": getattr(bar, "date", None),
                "open": getattr(bar, "open", None),
                "high": getattr(bar, "high", None),
                "low": getattr(bar, "low", None),
                "close": getattr(bar, "close", None),
                "volume": getattr(bar, "volume", None),
                "average": getattr(bar, "average", None),
                "bar_count": getattr(bar, "barCount", None),
            }
            for bar in bars or []
        ]
        frame = pd.DataFrame(rows)
        if not frame.empty:
            frame["date"] = pd.to_datetime(frame["date"], utc=True, errors="coerce")
            frame["symbol"] = getattr(ib_contract, "localSymbol", getattr(ib_contract, "symbol", ""))
            frame["underlying"] = getattr(ib_contract, "symbol", "")
        return frame

    def fetch_option_history_snapshots(
        self,
        symbol: str,
        *,
        exchange: str = "SMART",
        currency: str = "USD",
        trading_class: str | None = None,
        expirations: Sequence[date] | None = None,
        strikes: Sequence[float] | None = None,
        option_types: Sequence[OptionKind | str] | None = None,
        max_expiries: int | None = None,
        max_strikes: int | None = None,
        max_contracts: int | None = None,
        moneyness_window: float | None = None,
        duration_str: str = "2 D",
        bar_size: str = "5 mins",
        what_to_show: str = "TRADES",
        use_rth: bool = True,
    ) -> list[OptionSnapshot]:
        """Build current-ish option snapshots from IB historical bars when live quotes are unavailable."""

        underlying = self.qualify_equity(symbol, exchange=exchange, currency=currency)
        underlying_price = self._historical_market_price(
            underlying,
            duration_str=duration_str,
            bar_size=bar_size,
            what_to_show=what_to_show,
            use_rth=use_rth,
        )
        if underlying_price <= 0:
            underlying_price = self._snapshot_underlying_price(underlying)
        contracts = self.build_option_contracts(
            symbol,
            exchange=exchange,
            currency=currency,
            trading_class=trading_class,
            expirations=expirations,
            strikes=strikes,
            option_types=option_types,
            max_expiries=max_expiries,
            max_strikes=max_strikes,
            max_contracts=max_contracts,
            moneyness_window=moneyness_window,
            underlying_price=underlying_price,
        )
        if not contracts:
            return []
        snapshots: list[OptionSnapshot] = []
        for contract in contracts:
            bars = self._require_connected().reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration_str,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=2,
                keepUpToDate=False,
                chartOptions=[],
                timeout=max(self.config.timeout, 1.0) * 10.0,
            )
            if not bars:
                continue
            last_bar = bars[-1]
            market_price = float(getattr(last_bar, "close", 0.0) or 0.0)
            if market_price <= 0:
                continue
            expiry = _parse_ib_expiry(getattr(contract, "lastTradeDateOrContractMonth"))
            timestamp = pd.Timestamp(getattr(last_bar, "date", datetime.now(UTC)))
            timestamp = timestamp.tz_localize(UTC) if timestamp.tzinfo is None else timestamp.tz_convert(UTC)
            option_type = OptionKind.CALL if str(getattr(contract, "right", "")).upper().startswith("C") else OptionKind.PUT
            implied_vol = implied_volatility(
                market_price=market_price,
                spot=underlying_price,
                strike=float(getattr(contract, "strike")),
                time_to_expiry=max((expiry - timestamp.date()).days, 0) / 252.0,
                rate=self.config.risk_free_rate,
                dividend_yield=self.config.dividend_yield,
                option_type=option_type,
            )
            if implied_vol is None:
                lower_bound = _minimum_option_price(
                    spot=underlying_price,
                    strike=float(getattr(contract, "strike")),
                    time_to_expiry=max((expiry - timestamp.date()).days, 0) / 252.0,
                    rate=self.config.risk_free_rate,
                    dividend_yield=self.config.dividend_yield,
                    option_type=option_type,
                )
                implied_vol = implied_volatility(
                    market_price=max(market_price, lower_bound + 1e-4),
                    spot=underlying_price,
                    strike=float(getattr(contract, "strike")),
                    time_to_expiry=max((expiry - timestamp.date()).days, 0) / 252.0,
                    rate=self.config.risk_free_rate,
                    dividend_yield=self.config.dividend_yield,
                    option_type=option_type,
                )
            snapshots.append(
                OptionSnapshot(
                    contract=OptionContract(
                        symbol=str(getattr(contract, "localSymbol", getattr(contract, "symbol", ""))),
                        underlying=str(getattr(contract, "symbol", "")),
                        option_type=option_type,
                        strike=float(getattr(contract, "strike")),
                        expiry=expiry,
                        contract_multiplier=int(float(getattr(contract, "multiplier", 100) or 100)),
                        exercise_style="american",
                        exchange=str(getattr(contract, "exchange", "") or exchange),
                        currency=str(getattr(contract, "currency", currency) or currency),
                        contract_id=int(getattr(contract, "conId", 0) or 0),
                        local_symbol=str(getattr(contract, "localSymbol", getattr(contract, "symbol", ""))),
                        trading_class=str(getattr(contract, "tradingClass", "") or getattr(contract, "symbol", "")),
                    ),
                    quote=OptionQuote(
                        bid=market_price,
                        ask=market_price,
                        last=market_price,
                        volume=int(float(getattr(last_bar, "volume", 0.0) or 0.0)),
                        open_interest=None,
                    ),
                    timestamp=timestamp.date(),
                    underlying_price=underlying_price,
                    risk_free_rate=self.config.risk_free_rate,
                    dividend_yield=self.config.dividend_yield,
                    implied_vol=implied_vol,
                    market="US",
                )
            )
        return snapshots

    def place_option_order(
        self,
        contract: OptionContract | OptionSnapshot,
        request: IBOrderRequest,
    ) -> IBOrderReceipt:
        """Place a single-leg option order."""

        ib_contract = self._resolve_option_contract(contract)
        trade = self._require_connected().placeOrder(ib_contract, self._build_order(request))
        self._require_connected().sleep(self.config.order_wait_seconds)
        return _trade_to_receipt(
            trade,
            symbol=getattr(ib_contract, "localSymbol", getattr(ib_contract, "symbol", "")),
            request=request,
        )

    def place_spread_order(
        self,
        *,
        underlying_symbol: str,
        legs: Sequence[IBSpreadLeg],
        request: IBOrderRequest,
        currency: str = "USD",
        exchange: str = "SMART",
    ) -> IBOrderReceipt:
        """Place a multi-leg options spread as an IB BAG/Combo order."""

        if not legs:
            raise ValueError("Spread orders require at least one leg.")
        runtime = _load_ib_async()
        resolved_legs = []
        for leg in legs:
            ib_contract = self._resolve_option_contract(leg.contract)
            resolved_legs.append(
                runtime["ComboLeg"](
                    conId=int(getattr(ib_contract, "conId")),
                    ratio=int(leg.ratio),
                    action=leg.action.upper(),
                    exchange=leg.exchange or exchange,
                )
            )
        combo_contract = runtime["Bag"](
            symbol=underlying_symbol,
            exchange=exchange,
            currency=currency,
        )
        combo_contract.comboLegs = resolved_legs
        trade = self._require_connected().placeOrder(combo_contract, self._build_order(request))
        self._require_connected().sleep(self.config.order_wait_seconds)
        return _trade_to_receipt(trade, symbol=underlying_symbol, request=request)

    def positions_with_greeks(self) -> list[IBPositionSnapshot]:
        """Return live option positions with IB-provided Greeks."""

        ib = self._require_connected()
        portfolio = {
            int(getattr(item.contract, "conId")): item
            for item in _safe_call(ib, "portfolio", default=[])
            if getattr(getattr(item, "contract", None), "secType", "") == "OPT"
        }
        positions = [
            position
            for position in _safe_call(ib, "positions", default=[])
            if getattr(getattr(position, "contract", None), "secType", "") == "OPT"
        ]
        if not positions:
            return []
        tickers = []
        for position in positions:
            tickers.append(
                ib.reqMktData(
                    position.contract,
                    genericTickList=self.config.generic_ticks,
                    snapshot=True,
                    regulatorySnapshot=False,
                    mktDataOptions=[],
                )
            )
        ib.sleep(self.config.quote_wait_seconds)
        results: list[IBPositionSnapshot] = []
        for position, ticker in zip(positions, tickers):
            contract = position.contract
            snapshot = self._option_snapshot_from_market_data(contract=contract, ticker=ticker)
            portfolio_item = portfolio.get(int(getattr(contract, "conId", 0)))
            results.append(
                IBPositionSnapshot(
                    account=str(getattr(position, "account", "")),
                    symbol=getattr(contract, "localSymbol", getattr(contract, "symbol", "")),
                    underlying=str(getattr(contract, "symbol", "")),
                    quantity=float(getattr(position, "position", 0.0)),
                    avg_cost=float(getattr(position, "avgCost", 0.0)),
                    market_price=None if portfolio_item is None else float(getattr(portfolio_item, "marketPrice", 0.0)),
                    market_value=None if portfolio_item is None else float(getattr(portfolio_item, "marketValue", 0.0)),
                    unrealized_pnl=None if portfolio_item is None else float(getattr(portfolio_item, "unrealizedPNL", 0.0)),
                    realized_pnl=None if portfolio_item is None else float(getattr(portfolio_item, "realizedPNL", 0.0)),
                    greeks=None if snapshot is None else snapshot.broker_greeks,
                    snapshot=snapshot,
                )
            )
        return results

    def _resolve_option_contract(self, contract: OptionContract | OptionSnapshot) -> Any:
        option_contract = contract.contract if isinstance(contract, OptionSnapshot) else contract
        runtime = _load_ib_async()
        ib_contract = runtime["Option"](
            option_contract.underlying,
            pd.Timestamp(option_contract.expiry).strftime("%Y%m%d"),
            float(option_contract.strike),
            "C" if option_contract.option_type == OptionKind.CALL else "P",
            option_contract.exchange or self.config.option_exchange,
            str(option_contract.contract_multiplier),
            option_contract.currency,
            conId=option_contract.contract_id or 0,
            localSymbol=option_contract.local_symbol or "",
            tradingClass=option_contract.trading_class or "",
        )
        return self._qualify_contract(ib_contract) if not getattr(ib_contract, "conId", 0) else ib_contract

    def _qualify_contract(self, contract: Any) -> Any:
        qualified = self._require_connected().qualifyContracts(contract)
        if qualified:
            return qualified[0]
        raise RuntimeError(f"Unable to qualify IB contract: {contract!r}")

    def _snapshot_underlying_price(self, contract: Any) -> float:
        ib = self._require_connected()
        tickers = ib.reqTickers(contract)
        ticker = tickers[0] if tickers else None
        price = _ticker_market_price(ticker)
        if price > 0:
            return price
        return self._historical_market_price(
            contract,
            duration_str="5 D",
            bar_size="1 day",
            what_to_show="TRADES",
            use_rth=True,
        )

    def _historical_market_price(
        self,
        contract: Any,
        *,
        duration_str: str,
        bar_size: str,
        what_to_show: str,
        use_rth: bool,
    ) -> float:
        bars = self._require_connected().reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration_str,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=2,
            keepUpToDate=False,
            chartOptions=[],
            timeout=max(self.config.timeout, 1.0) * 10.0,
        )
        if not bars:
            return 0.0
        last_bar = bars[-1]
        for field in ("close", "average", "open"):
            value = getattr(last_bar, field, None)
            if value is not None and float(value) > 0:
                return float(value)
        return 0.0

    def _option_snapshot_from_market_data(
        self,
        *,
        contract: Any,
        ticker: Any,
        fallback_underlying_price: float | None = None,
    ) -> OptionSnapshot | None:
        quote = OptionQuote(
            bid=_coerce_price(getattr(ticker, "bid", None)),
            ask=_coerce_price(getattr(ticker, "ask", None)),
            last=_coerce_optional_price(getattr(ticker, "last", None)),
            volume=_option_volume(ticker, getattr(contract, "right", "")),
            open_interest=_option_open_interest(ticker, getattr(contract, "right", "")),
        )
        if quote.bid <= 0 and quote.ask <= 0 and quote.last is None:
            return None
        greeks = _select_greeks(ticker)
        underlying_price = _underlying_price_from_market_data(greeks, fallback_underlying_price)
        if underlying_price <= 0:
            return None
        option_type = OptionKind.CALL if str(getattr(contract, "right", "")).upper().startswith("C") else OptionKind.PUT
        expiry = _parse_ib_expiry(getattr(contract, "lastTradeDateOrContractMonth"))
        return OptionSnapshot(
            contract=OptionContract(
                symbol=str(getattr(contract, "localSymbol", getattr(contract, "symbol", ""))),
                underlying=str(getattr(contract, "symbol", "")),
                option_type=option_type,
                strike=float(getattr(contract, "strike")),
                expiry=expiry,
                contract_multiplier=int(float(getattr(contract, "multiplier", 100) or 100)),
                exercise_style="american",
                exchange=str(getattr(contract, "exchange", "") or self.config.option_exchange),
                currency=str(getattr(contract, "currency", "USD") or "USD"),
                contract_id=int(getattr(contract, "conId", 0) or 0),
                local_symbol=str(getattr(contract, "localSymbol", getattr(contract, "symbol", ""))),
                trading_class=str(getattr(contract, "tradingClass", "") or getattr(contract, "symbol", "")),
            ),
            quote=quote,
            timestamp=_ticker_timestamp(ticker),
            underlying_price=underlying_price,
            risk_free_rate=self.config.risk_free_rate,
            dividend_yield=self.config.dividend_yield,
            implied_vol=_implied_vol_from_market_data(ticker, greeks),
            market="US",
            broker_greeks=None if greeks is None else _greeks_to_vector(greeks),
        )

    def _build_order(self, request: IBOrderRequest) -> Any:
        runtime = _load_ib_async()
        order_type = request.order_type.upper()
        kwargs: dict[str, object] = {
            "tif": request.tif,
            "transmit": request.transmit,
        }
        if request.account or self.config.account:
            kwargs["account"] = request.account or self.config.account
        if request.order_ref:
            kwargs["orderRef"] = request.order_ref
        if order_type == "MKT":
            return runtime["MarketOrder"](request.action.upper(), request.quantity, **kwargs)
        if order_type == "LMT":
            if request.limit_price is None:
                raise ValueError("Limit orders require limit_price.")
            return runtime["LimitOrder"](
                request.action.upper(),
                request.quantity,
                request.limit_price,
                **kwargs,
            )
        raise ValueError(f"Unsupported IB order type: {request.order_type}")

    @staticmethod
    def _select_chain(
        chains: Sequence[IBOptionChain],
        *,
        preferred_exchange: str,
        preferred_trading_class: str | None,
    ) -> IBOptionChain:
        if preferred_trading_class:
            for chain in chains:
                if chain.trading_class == preferred_trading_class:
                    return chain
        for chain in chains:
            if chain.exchange == preferred_exchange:
                return chain
        return chains[0]

    def _require_connected(self) -> Any:
        if not self._connected or self._ib is None:
            raise RuntimeError("IB Gateway client is not connected.")
        return self._ib

    @staticmethod
    def _default_ib_factory() -> Any:
        return _load_ib_async()["IB"]()


def _load_ib_async() -> dict[str, Any]:
    try:
        from ib_async import Bag, ComboLeg, IB, LimitOrder, MarketOrder, Option, Stock
    except ImportError as error:  # pragma: no cover - exercised by runtime setup, not unit tests
        raise RuntimeError(
            "ib_async is required for IB Gateway support. Install it with `pip install ib_async`."
        ) from error
    return {
        "Bag": Bag,
        "ComboLeg": ComboLeg,
        "IB": IB,
        "LimitOrder": LimitOrder,
        "MarketOrder": MarketOrder,
        "Option": Option,
        "Stock": Stock,
    }


def _normalize_option_types(option_types: Sequence[OptionKind | str] | None) -> tuple[OptionKind, ...]:
    if not option_types:
        return (OptionKind.CALL, OptionKind.PUT)
    normalized: list[OptionKind] = []
    for value in option_types:
        normalized.append(value if isinstance(value, OptionKind) else OptionKind(str(value).lower()))
    return tuple(normalized)


def _parse_ib_expiry(value: object) -> date:
    text = str(value)
    fmt = "%Y%m%d" if len(text) == 8 else "%Y%m"
    return datetime.strptime(text, fmt).date()


def _ticker_timestamp(ticker: Any) -> date:
    timestamp = getattr(ticker, "time", None)
    if timestamp is None:
        return datetime.now(UTC).date()
    return pd.Timestamp(timestamp).date()


def _ticker_market_price(ticker: Any) -> float:
    if ticker is None:
        return 0.0
    market_price = getattr(ticker, "marketPrice", None)
    if callable(market_price):
        try:
            value = float(market_price())
            if value > 0:
                return value
        except Exception:  # pragma: no cover - defensive fallback
            pass
    for field in ("last", "close", "bid", "ask"):
        value = _coerce_optional_price(getattr(ticker, field, None))
        if value is not None and value > 0:
            return value
    return 0.0


def _select_greeks(ticker: Any) -> Any | None:
    for field in ("modelGreeks", "lastGreeks", "bidGreeks", "askGreeks"):
        greeks = getattr(ticker, field, None)
        if greeks is not None and any(
            getattr(greeks, attribute, None) is not None
            for attribute in ("delta", "gamma", "vega", "theta", "impliedVol", "undPrice")
        ):
            return greeks
    return None


def _underlying_price_from_market_data(greeks: Any | None, fallback: float | None) -> float:
    if greeks is not None:
        under_price = getattr(greeks, "undPrice", None)
        if under_price is not None and float(under_price) > 0:
            return float(under_price)
    if fallback is not None and fallback > 0:
        return float(fallback)
    return 0.0


def _implied_vol_from_market_data(ticker: Any, greeks: Any | None) -> float | None:
    if greeks is not None and getattr(greeks, "impliedVol", None) is not None:
        return float(greeks.impliedVol)
    ticker_iv = getattr(ticker, "impliedVolatility", None)
    if ticker_iv is None:
        return None
    return float(ticker_iv)


def _greeks_to_vector(greeks: Any) -> GreekVector:
    return GreekVector(
        delta=float(getattr(greeks, "delta", 0.0) or 0.0),
        gamma=float(getattr(greeks, "gamma", 0.0) or 0.0),
        vega=float(getattr(greeks, "vega", 0.0) or 0.0),
        theta=float(getattr(greeks, "theta", 0.0) or 0.0),
    )


def _option_volume(ticker: Any, right: str) -> int:
    if str(right).upper().startswith("C"):
        value = getattr(ticker, "callVolume", None)
    else:
        value = getattr(ticker, "putVolume", None)
    if value is None:
        value = getattr(ticker, "volume", 0)
    return int(float(value or 0))


def _option_open_interest(ticker: Any, right: str) -> int | None:
    if str(right).upper().startswith("C"):
        value = getattr(ticker, "callOpenInterest", None)
    else:
        value = getattr(ticker, "putOpenInterest", None)
    if value is None:
        return None
    return int(float(value))


def _trade_to_receipt(trade: Any, *, symbol: str, request: IBOrderRequest) -> IBOrderReceipt:
    order = getattr(trade, "order", None)
    order_status = getattr(trade, "orderStatus", None)
    return IBOrderReceipt(
        order_id=None if order is None else getattr(order, "orderId", None),
        perm_id=(
            None
            if order_status is None
            else getattr(order_status, "permId", getattr(order, "permId", None))
        ),
        symbol=symbol,
        action=request.action.upper(),
        order_type=request.order_type.upper(),
        status="Submitted" if order_status is None else str(getattr(order_status, "status", "Submitted")),
        filled=0.0 if order_status is None else float(getattr(order_status, "filled", 0.0) or 0.0),
        remaining=0.0 if order_status is None else float(getattr(order_status, "remaining", 0.0) or 0.0),
        avg_fill_price=0.0 if order_status is None else float(getattr(order_status, "avgFillPrice", 0.0) or 0.0),
    )


def _nearest_strikes(strikes: Iterable[float], *, reference: float, limit: int) -> list[float]:
    ordered = sorted(float(value) for value in strikes)
    ranked = sorted(ordered, key=lambda value: abs(value - reference))
    return sorted(ranked[:limit])


def _chunked(values: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _coerce_price(value: object) -> float:
    if value is None:
        return 0.0
    return max(float(value), 0.0)


def _coerce_optional_price(value: object) -> float | None:
    if value is None:
        return None
    coerced = float(value)
    return None if coerced <= 0 else coerced


def _safe_call(obj: Any, method_name: str, *, default: Any) -> Any:
    method = getattr(obj, method_name, None)
    if method is None:
        return default
    return method()


def _intrinsic_option_price(*, spot: float, strike: float, option_type: OptionKind) -> float:
    if option_type == OptionKind.CALL:
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


def _minimum_option_price(
    *,
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    dividend_yield: float,
    option_type: OptionKind,
) -> float:
    discounted_spot = spot * exp(-dividend_yield * time_to_expiry)
    discounted_strike = strike * exp(-rate * time_to_expiry)
    if option_type == OptionKind.CALL:
        return max(discounted_spot - discounted_strike, 0.0)
    return max(discounted_strike - discounted_spot, 0.0)
