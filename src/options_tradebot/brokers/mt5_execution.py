"""MT5 execution routing for later live deployment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MT5ExecutionConfig:
    """Live or demo order-routing configuration."""

    login: int | None = None
    password: str | None = None
    server: str | None = None
    path: str | None = None
    magic_number: int = 246810
    deviation: int = 20
    require_demo: bool = True


class MT5ExecutionGateway:
    """Market-order adapter for MT5."""

    def __init__(self, config: MT5ExecutionConfig):
        self.config = config
        self._mt5: Any | None = None
        self._connected = False

    def connect(self) -> dict[str, object]:
        """Connect to MT5 and verify the account when requested."""

        mt5 = self._import_mt5()
        kwargs: dict[str, object] = {}
        if self.config.path:
            kwargs["path"] = self.config.path
        if self.config.login is not None:
            kwargs["login"] = self.config.login
        if self.config.password:
            kwargs["password"] = self.config.password
        if self.config.server:
            kwargs["server"] = self.config.server
        if not mt5.initialize(**kwargs):
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        self._mt5 = mt5
        self._connected = True
        account = mt5.account_info()
        if account is None:
            raise RuntimeError("MT5 account_info() returned no data.")
        if self.config.require_demo:
            demo_mode = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", None)
            if demo_mode is not None and getattr(account, "trade_mode", None) != demo_mode:
                server_name = str(getattr(account, "server", ""))
                if "DEMO" not in server_name.upper():
                    raise RuntimeError(
                        "Refusing to route orders to a non-demo MT5 account while require_demo=True."
                    )
        return account._asdict()

    def shutdown(self) -> None:
        if self._mt5 is not None:
            self._mt5.shutdown()
        self._connected = False
        self._mt5 = None

    def get_position(self, symbol: str) -> float:
        """Return the signed position for one symbol."""

        mt5 = self._require_connected()
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return 0.0
        signed = 0.0
        for position in positions:
            signed += float(position.volume) if int(position.type) == 0 else -float(position.volume)
        return signed

    def send_market_order(self, *, symbol: str, side: str, volume: float, comment: str) -> dict[str, object]:
        """Send a market order to MT5."""

        mt5 = self._require_connected()
        if side.upper() not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            raise ValueError(f"Unknown MT5 symbol: {symbol}")
        if not symbol_info.visible:
            mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"Unable to read the latest tick for {symbol}")
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_BUY if side.upper() == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": tick.ask if side.upper() == "BUY" else tick.bid,
            "deviation": self.config.deviation,
            "magic": self.config.magic_number,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"MT5 order_send() returned None: {mt5.last_error()}")
        return result._asdict()

    def _require_connected(self):
        if not self._connected or self._mt5 is None:
            raise RuntimeError("MT5 execution gateway is not connected.")
        return self._mt5

    @staticmethod
    def _import_mt5():
        import MetaTrader5 as mt5  # noqa: N813

        return mt5
