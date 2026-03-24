from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_tradebot.connectors.ib import (
    IBGatewayClient,
    IBGatewayConfig,
    IBOrderRequest,
    IBSpreadLeg,
)
from options_tradebot.market import OptionContract, OptionKind


class FakeStock:
    def __init__(self, symbol: str, exchange: str, currency: str):
        self.symbol = symbol
        self.exchange = exchange
        self.currency = currency
        self.secType = "STK"
        self.conId = 0


class FakeOption:
    def __init__(
        self,
        symbol: str,
        lastTradeDateOrContractMonth: str,
        strike: float,
        right: str,
        exchange: str,
        multiplier: str,
        currency: str,
        **kwargs,
    ):
        self.symbol = symbol
        self.lastTradeDateOrContractMonth = lastTradeDateOrContractMonth
        self.strike = strike
        self.right = right
        self.exchange = exchange
        self.multiplier = multiplier
        self.currency = currency
        self.secType = "OPT"
        self.conId = kwargs.get("conId", 0)
        self.localSymbol = kwargs.get("localSymbol", "")
        self.tradingClass = kwargs.get("tradingClass", "")


class FakeBag:
    def __init__(self, symbol: str, exchange: str, currency: str):
        self.symbol = symbol
        self.exchange = exchange
        self.currency = currency
        self.secType = "BAG"
        self.comboLegs = []


class FakeComboLeg:
    def __init__(self, conId: int, ratio: int, action: str, exchange: str):
        self.conId = conId
        self.ratio = ratio
        self.action = action
        self.exchange = exchange


class FakeLimitOrder:
    def __init__(self, action: str, totalQuantity: float, lmtPrice: float, **kwargs):
        self.action = action
        self.totalQuantity = totalQuantity
        self.lmtPrice = lmtPrice
        self.orderId = 101
        self.permId = 9001
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeMarketOrder:
    def __init__(self, action: str, totalQuantity: float, **kwargs):
        self.action = action
        self.totalQuantity = totalQuantity
        self.orderId = 102
        self.permId = 9002
        for key, value in kwargs.items():
            setattr(self, key, value)


@dataclass
class FakeChain:
    exchange: str = "SMART"
    tradingClass: str = "PBR"
    multiplier: str = "100"
    expirations: tuple[str, ...] = ("20260417",)
    strikes: tuple[float, ...] = (12.0, 13.0)


@dataclass
class FakeGreeks:
    delta: float
    gamma: float
    vega: float
    theta: float
    impliedVol: float
    undPrice: float


class FakeTicker:
    def __init__(self, **kwargs):
        self.time = kwargs.pop("time", datetime(2026, 3, 24, 14, 30))
        self._market_price = kwargs.pop("_market_price", None)
        for key, value in kwargs.items():
            setattr(self, key, value)

    def marketPrice(self) -> float:
        return float(self._market_price or 0.0)


@dataclass
class FakeOrderStatus:
    status: str = "Submitted"
    filled: float = 0.0
    remaining: float = 1.0
    avgFillPrice: float = 0.0
    permId: int = 9001


@dataclass
class FakeTrade:
    order: object
    orderStatus: FakeOrderStatus


class FakeIB:
    def __init__(self):
        self.connected = False
        self.market_data_type = None
        self.last_contract = None
        self.last_order = None

    def connect(self, host: str, port: int, clientId: int, timeout: float) -> None:
        self.connected = True
        self.host = host
        self.port = port
        self.clientId = clientId
        self.timeout = timeout

    def disconnect(self) -> None:
        self.connected = False

    def isConnected(self) -> bool:
        return self.connected

    def managedAccounts(self) -> list[str]:
        return ["DU123456"]

    def reqMarketDataType(self, market_data_type: int) -> None:
        self.market_data_type = market_data_type

    def qualifyContracts(self, *contracts):
        qualified = []
        for index, contract in enumerate(contracts, start=1):
            if getattr(contract, "secType", "") == "STK":
                contract.conId = 500 + index
            else:
                contract.conId = contract.conId or 1000 + index
                if not getattr(contract, "localSymbol", ""):
                    contract.localSymbol = (
                        f"{contract.symbol}_{contract.right}_{int(float(contract.strike) * 100)}"
                    )
                if not getattr(contract, "tradingClass", ""):
                    contract.tradingClass = contract.symbol
            qualified.append(contract)
        return qualified

    def reqSecDefOptParams(self, symbol: str, futFopExchange: str, secType: str, conId: int):
        return [FakeChain()]

    def reqTickers(self, *contracts):
        return [FakeTicker(_market_price=12.34, last=12.34, bid=12.33, ask=12.35)]

    def reqMktData(self, contract, genericTickList: str, snapshot: bool, regulatorySnapshot: bool, mktDataOptions):
        greeks = FakeGreeks(delta=0.42, gamma=0.08, vega=0.15, theta=-0.02, impliedVol=0.27, undPrice=12.34)
        right = getattr(contract, "right", "C")
        if str(right).upper().startswith("C"):
            return FakeTicker(
                bid=1.10,
                ask=1.18,
                last=1.14,
                callVolume=1_200,
                callOpenInterest=4_500,
                modelGreeks=greeks,
            )
        return FakeTicker(
            bid=0.82,
            ask=0.90,
            last=0.86,
            putVolume=900,
            putOpenInterest=3_900,
            modelGreeks=greeks,
        )

    def reqHistoricalData(self, *args, **kwargs):
        return []

    def placeOrder(self, contract, order):
        self.last_contract = contract
        self.last_order = order
        return FakeTrade(order=order, orderStatus=FakeOrderStatus(remaining=float(order.totalQuantity)))

    def sleep(self, seconds: float) -> None:
        self.slept = seconds

    def cancelMktData(self, contract) -> None:
        self.cancelled_contract = contract

    def portfolio(self):
        return []

    def positions(self):
        return []


class IBGatewayClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = {
            "Bag": FakeBag,
            "ComboLeg": FakeComboLeg,
            "IB": FakeIB,
            "LimitOrder": FakeLimitOrder,
            "MarketOrder": FakeMarketOrder,
            "Option": FakeOption,
            "Stock": FakeStock,
        }

    def test_fetch_option_snapshots_uses_ib_market_data_and_greeks(self) -> None:
        fake_ib = FakeIB()
        with patch("options_tradebot.connectors.ib._load_ib_async", return_value=self.runtime):
            client = IBGatewayClient(
                IBGatewayConfig(port=7497, risk_free_rate=0.045),
                ib_factory=lambda: fake_ib,
            )
            client.connect()
            snapshots = client.fetch_option_snapshots(
                "PBR",
                max_expiries=1,
                max_strikes=1,
                option_types=[OptionKind.CALL],
            )
        self.assertEqual(len(snapshots), 1)
        snapshot = snapshots[0]
        self.assertEqual(snapshot.market, "US")
        self.assertEqual(snapshot.contract.underlying, "PBR")
        self.assertEqual(snapshot.contract.currency, "USD")
        self.assertEqual(snapshot.quote.open_interest, 4500)
        self.assertEqual(snapshot.quote.volume, 1200)
        self.assertIsNotNone(snapshot.broker_greeks)
        self.assertAlmostEqual(snapshot.broker_greeks.delta, 0.42, places=6)
        self.assertAlmostEqual(snapshot.implied_vol or 0.0, 0.27, places=6)

    def test_place_spread_order_builds_combo_contract(self) -> None:
        fake_ib = FakeIB()
        with patch("options_tradebot.connectors.ib._load_ib_async", return_value=self.runtime):
            client = IBGatewayClient(IBGatewayConfig(), ib_factory=lambda: fake_ib)
            client.connect()
            short_call = OptionContract(
                symbol="PBR_C_1300",
                underlying="PBR",
                option_type=OptionKind.CALL,
                strike=13.0,
                expiry=datetime(2026, 4, 17).date(),
                exchange="SMART",
                currency="USD",
                contract_id=1101,
                local_symbol="PBR_C_1300",
                trading_class="PBR",
            )
            long_call = OptionContract(
                symbol="PBR_C_1350",
                underlying="PBR",
                option_type=OptionKind.CALL,
                strike=13.5,
                expiry=datetime(2026, 4, 17).date(),
                exchange="SMART",
                currency="USD",
                contract_id=1102,
                local_symbol="PBR_C_1350",
                trading_class="PBR",
            )
            receipt = client.place_spread_order(
                underlying_symbol="PBR",
                legs=[
                    IBSpreadLeg(contract=short_call, action="SELL"),
                    IBSpreadLeg(contract=long_call, action="BUY"),
                ],
                request=IBOrderRequest(action="SELL", quantity=1, order_type="LMT", limit_price=0.22),
            )
        self.assertEqual(receipt.order_type, "LMT")
        self.assertEqual(receipt.symbol, "PBR")
        self.assertEqual(len(fake_ib.last_contract.comboLegs), 2)
        self.assertEqual(fake_ib.last_contract.comboLegs[0].action, "SELL")
        self.assertEqual(fake_ib.last_contract.comboLegs[1].action, "BUY")
        self.assertAlmostEqual(fake_ib.last_order.lmtPrice, 0.22, places=6)
