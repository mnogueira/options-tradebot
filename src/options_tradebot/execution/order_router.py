"""Mode-aware order routing for defined-risk short-vol trades."""

from __future__ import annotations

from contextlib import AbstractContextManager

from options_tradebot.config.schema import ShortVolRuntimeConfig
from options_tradebot.execution.ib_adapter import IBExecutionAdapter
from options_tradebot.execution.mt5_adapter import MT5ExecutionAdapter
from options_tradebot.execution.sim_adapter import SimExecutionAdapter
from options_tradebot.market.models import OptionSnapshot
from options_tradebot.strategies.defined_risk.types import ManagedPosition, StrategyTradeCandidate


class OrderRouter(AbstractContextManager["OrderRouter"]):
    def __init__(self, config: ShortVolRuntimeConfig, *, mode: str):
        self.config = config
        self.mode = mode
        self._sim = SimExecutionAdapter()
        self._ib_adapter: IBExecutionAdapter | None = None
        self._mt5_adapter: MT5ExecutionAdapter | None = None

    def __enter__(self) -> "OrderRouter":
        if self.mode == "paper-broker" and not self.config.execution.allow_paper_broker_orders:
            raise RuntimeError("Paper broker execution is disabled in config.")
        if self.mode == "live" and not self.config.execution.allow_live_orders:
            raise RuntimeError("Live execution is disabled in config.")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._ib_adapter is not None:
            self._ib_adapter.disconnect()
        if self._mt5_adapter is not None:
            self._mt5_adapter.disconnect()
        return None

    def open_candidate(
        self,
        candidate: StrategyTradeCandidate,
        *,
        snapshot_map: dict[str, OptionSnapshot],
    ) -> tuple[ManagedPosition, tuple[dict[str, object], ...]]:
        if self.mode == "sim":
            return self._sim.open_candidate(candidate)
        if candidate.venue == "ib":
            self._ensure_ib_adapter()
            return self._ib_adapter.open_candidate(candidate, snapshot_map=snapshot_map, mode=self.mode)
        if candidate.venue == "mt5":
            self._ensure_mt5_adapter()
            return self._mt5_adapter.open_candidate(candidate, mode=self.mode)
        raise ValueError(f"Unsupported venue for candidate: {candidate.venue}")

    def close_position(
        self,
        position: ManagedPosition,
        *,
        snapshot_map: dict[str, OptionSnapshot],
        close_debit: float,
        exit_reason: str,
    ) -> tuple[ManagedPosition, tuple[dict[str, object], ...]]:
        if self.mode == "sim":
            return self._sim.close_position(position, close_debit=close_debit, exit_reason=exit_reason)
        if position.venue == "ib":
            self._ensure_ib_adapter()
            return self._ib_adapter.close_position(
                position,
                snapshot_map=snapshot_map,
                close_debit=close_debit,
                exit_reason=exit_reason,
                mode=self.mode,
            )
        if position.venue == "mt5":
            self._ensure_mt5_adapter()
            return self._mt5_adapter.close_position(
                position,
                close_debit=close_debit,
                exit_reason=exit_reason,
                mode=self.mode,
            )
        raise ValueError(f"Unsupported venue for position: {position.venue}")

    def _ensure_ib_adapter(self) -> None:
        if self._ib_adapter is not None:
            return
        self._ib_adapter = IBExecutionAdapter(
            data_endpoint=self.config.venues.ib.data,
            execution_endpoint=self.config.venues.ib.paper if self.mode == "paper-broker" else self.config.venues.ib.live,
            pricing=self.config.pricing,
            execution=self.config.execution,
        )
        self._ib_adapter.connect()

    def _ensure_mt5_adapter(self) -> None:
        if self._mt5_adapter is not None:
            return
        self._mt5_adapter = MT5ExecutionAdapter(
            endpoint=self.config.venues.mt5.paper if self.mode == "paper-broker" else self.config.venues.mt5.live,
            execution=self.config.execution,
        )
        self._mt5_adapter.connect()
