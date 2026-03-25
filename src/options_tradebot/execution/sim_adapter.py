"""Local simulation adapter for defined-risk short-vol trades."""

from __future__ import annotations

from datetime import UTC, datetime

from options_tradebot.strategies.defined_risk.types import ManagedPosition, StrategyTradeCandidate


class SimExecutionAdapter:
    def open_candidate(self, candidate: StrategyTradeCandidate) -> tuple[ManagedPosition, tuple[dict[str, object], ...]]:
        timestamp = datetime.now(UTC).isoformat()
        receipt = {
            "timestamp": timestamp,
            "mode": "sim",
            "action": "OPEN",
            "strategy_name": candidate.strategy_name,
            "candidate_id": candidate.candidate_id,
            "underlying": candidate.underlying,
            "contracts": candidate.contracts,
            "entry_credit": candidate.entry_credit,
        }
        return ManagedPosition.from_candidate(candidate, mode="sim", opened_at=timestamp, order_payloads=(receipt,)), (receipt,)

    def close_position(
        self,
        position: ManagedPosition,
        *,
        close_debit: float,
        exit_reason: str,
    ) -> tuple[ManagedPosition, tuple[dict[str, object], ...]]:
        timestamp = datetime.now(UTC).isoformat()
        multiplier = position.legs[0].contract_multiplier if position.legs else 100
        pnl = (position.entry_credit - close_debit) * multiplier * position.contracts
        receipt = {
            "timestamp": timestamp,
            "mode": "sim",
            "action": "CLOSE",
            "position_id": position.position_id,
            "underlying": position.underlying,
            "close_debit": close_debit,
            "exit_reason": exit_reason,
            "realized_pnl": pnl,
        }
        return position.close(closed_at=timestamp, exit_reason=exit_reason, realized_pnl=pnl, last_mark=close_debit), (receipt,)
