"""Interactive Brokers execution adapter for defined-risk spreads."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from options_tradebot.config.schema import ExecutionConfig, IBDataEndpointConfig, IBExecutionEndpointConfig, PricingConfig
from options_tradebot.connectors.ib import IBGatewayClient, IBGatewayConfig, IBOrderRequest, IBSpreadLeg
from options_tradebot.market.models import OptionSnapshot
from options_tradebot.strategies.defined_risk.types import ManagedPosition, StrategyTradeCandidate


class IBExecutionAdapter:
    def __init__(
        self,
        *,
        data_endpoint: IBDataEndpointConfig,
        execution_endpoint: IBExecutionEndpointConfig,
        pricing: PricingConfig,
        execution: ExecutionConfig,
    ):
        self._client = IBGatewayClient(
            IBGatewayConfig(
                host=data_endpoint.host,
                data_port=data_endpoint.port,
                data_client_id=data_endpoint.client_id,
                execution_port=execution_endpoint.port,
                execution_client_id=execution_endpoint.client_id,
                account=execution_endpoint.account or data_endpoint.account,
                market_data_type=data_endpoint.market_data_type,
                risk_free_rate=pricing.usd_risk_free_rate,
                read_only_data_only=False,
            )
        )
        self._execution = execution

    def connect(self) -> None:
        self._client.connect()

    def disconnect(self) -> None:
        self._client.disconnect()

    def open_candidate(
        self,
        candidate: StrategyTradeCandidate,
        *,
        snapshot_map: dict[str, OptionSnapshot],
        mode: str,
    ) -> tuple[ManagedPosition, tuple[dict[str, object], ...]]:
        legs = tuple(IBSpreadLeg(contract=snapshot_map[leg.symbol], action=leg.action) for leg in candidate.legs)
        receipt = self._client.place_spread_order(
            underlying_symbol=candidate.underlying,
            legs=legs,
            request=IBOrderRequest(
                action="SELL",
                quantity=candidate.contracts,
                order_type=self._execution.order_type,
                limit_price=self._limit_price(candidate.entry_credit, side="open_credit"),
                tif="DAY",
                order_ref=f"{self._execution.tag}:{candidate.strategy_name}:{candidate.underlying}",
            ),
            currency=snapshot_map[candidate.legs[0].symbol].contract.currency,
            exchange=snapshot_map[candidate.legs[0].symbol].contract.exchange or "SMART",
        )
        payload = asdict(receipt)
        return ManagedPosition.from_candidate(candidate, mode=mode, order_payloads=(payload,)), (payload,)

    def close_position(
        self,
        position: ManagedPosition,
        *,
        snapshot_map: dict[str, OptionSnapshot],
        close_debit: float,
        exit_reason: str,
        mode: str,
    ) -> tuple[ManagedPosition, tuple[dict[str, object], ...]]:
        reverse_legs = tuple(
            IBSpreadLeg(
                contract=snapshot_map[leg.symbol],
                action="BUY" if leg.action.upper() == "SELL" else "SELL",
            )
            for leg in position.legs
        )
        receipt = self._client.place_spread_order(
            underlying_symbol=position.underlying,
            legs=reverse_legs,
            request=IBOrderRequest(
                action="BUY",
                quantity=position.contracts,
                order_type=self._execution.order_type,
                limit_price=self._limit_price(close_debit, side="close_debit"),
                tif="DAY",
                order_ref=f"{self._execution.tag}:close:{position.position_id}",
            ),
            currency=snapshot_map[position.legs[0].symbol].contract.currency,
            exchange=snapshot_map[position.legs[0].symbol].contract.exchange or "SMART",
        )
        payload = asdict(receipt)
        multiplier = position.legs[0].contract_multiplier if position.legs else 100
        pnl = (position.entry_credit - close_debit) * multiplier * position.contracts
        return (
            position.close(
                closed_at=datetime.now(UTC).isoformat(),
                exit_reason=exit_reason,
                realized_pnl=pnl,
                last_mark=close_debit,
            ),
            (payload,),
        )

    def _limit_price(self, raw_price: float, *, side: str) -> float | None:
        if self._execution.order_type == "MKT":
            return None
        adjusted = max(raw_price, 0.0)
        if side == "open_credit":
            adjusted *= max(1.0 - self._execution.price_buffer_pct, 0.0)
        else:
            adjusted *= 1.0 + max(self._execution.price_buffer_pct, 0.0)
        return round(adjusted, self._execution.price_rounding)
