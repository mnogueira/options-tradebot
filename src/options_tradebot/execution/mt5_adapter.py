"""MetaTrader 5 execution adapter for legged defined-risk spreads."""

from __future__ import annotations

from datetime import UTC, datetime

from options_tradebot.brokers.mt5_execution import MT5ExecutionConfig, MT5ExecutionGateway
from options_tradebot.config.schema import ExecutionConfig, MT5EndpointConfig
from options_tradebot.strategies.defined_risk.types import ManagedPosition, StrategyTradeCandidate


class MT5ExecutionAdapter:
    def __init__(
        self,
        *,
        endpoint: MT5EndpointConfig,
        execution: ExecutionConfig,
    ):
        self._execution = execution
        self._gateway = MT5ExecutionGateway(
            MT5ExecutionConfig(
                login=endpoint.login,
                password=endpoint.password,
                server=endpoint.server,
                path=endpoint.path,
                require_demo=endpoint.require_demo,
            )
        )

    def connect(self) -> None:
        self._gateway.connect()

    def disconnect(self) -> None:
        self._gateway.shutdown()

    def open_candidate(self, candidate: StrategyTradeCandidate, *, mode: str) -> tuple[ManagedPosition, tuple[dict[str, object], ...]]:
        if not self._execution.mt5_legged_execution_enabled:
            raise RuntimeError("MT5 legged execution is disabled in config.")
        receipts: list[dict[str, object]] = []
        for leg in candidate.legs:
            receipts.append(
                self._gateway.send_market_order(
                    symbol=leg.symbol,
                    side=leg.action,
                    volume=float(candidate.contracts),
                    comment=f"{self._execution.tag}:{candidate.strategy_name}",
                )
            )
        return ManagedPosition.from_candidate(candidate, mode=mode, order_payloads=tuple(receipts)), tuple(receipts)

    def close_position(self, position: ManagedPosition, *, close_debit: float, exit_reason: str, mode: str) -> tuple[ManagedPosition, tuple[dict[str, object], ...]]:
        if not self._execution.mt5_legged_execution_enabled:
            raise RuntimeError("MT5 legged execution is disabled in config.")
        receipts: list[dict[str, object]] = []
        for leg in position.legs:
            reverse_side = "BUY" if leg.action.upper() == "SELL" else "SELL"
            receipts.append(
                self._gateway.send_market_order(
                    symbol=leg.symbol,
                    side=reverse_side,
                    volume=float(position.contracts),
                    comment=f"{self._execution.tag}:close:{position.position_id}",
                )
            )
        multiplier = position.legs[0].contract_multiplier if position.legs else 100
        pnl = (position.entry_credit - close_debit) * multiplier * position.contracts
        return position.close(closed_at=datetime.now(UTC).isoformat(), exit_reason=exit_reason, realized_pnl=pnl, last_mark=close_debit), tuple(receipts)
