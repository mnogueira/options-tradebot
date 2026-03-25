"""Shared dataclasses for defined-risk short-vol strategies."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class StrategyLeg:
    symbol: str
    action: str
    option_type: str
    strike: float
    expiry: str
    contract_multiplier: int

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "option_type": self.option_type,
            "strike": self.strike,
            "expiry": self.expiry,
            "contract_multiplier": self.contract_multiplier,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "StrategyLeg":
        return cls(
            symbol=str(payload["symbol"]),
            action=str(payload["action"]),
            option_type=str(payload["option_type"]),
            strike=float(payload["strike"]),
            expiry=str(payload["expiry"]),
            contract_multiplier=int(payload["contract_multiplier"]),
        )


@dataclass(frozen=True, slots=True)
class CandidateMetrics:
    expected_value: float
    expected_value_after_costs: float
    probability_of_profit: float
    probability_of_touch: float
    cvar_95: float
    return_on_risk: float
    liquidity_score: float
    vol_regime_score: float

    def to_dict(self) -> dict[str, float]:
        return {
            "expected_value": self.expected_value,
            "expected_value_after_costs": self.expected_value_after_costs,
            "probability_of_profit": self.probability_of_profit,
            "probability_of_touch": self.probability_of_touch,
            "cvar_95": self.cvar_95,
            "return_on_risk": self.return_on_risk,
            "liquidity_score": self.liquidity_score,
            "vol_regime_score": self.vol_regime_score,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "CandidateMetrics":
        return cls(
            expected_value=float(payload["expected_value"]),
            expected_value_after_costs=float(payload["expected_value_after_costs"]),
            probability_of_profit=float(payload["probability_of_profit"]),
            probability_of_touch=float(payload["probability_of_touch"]),
            cvar_95=float(payload["cvar_95"]),
            return_on_risk=float(payload["return_on_risk"]),
            liquidity_score=float(payload["liquidity_score"]),
            vol_regime_score=float(payload["vol_regime_score"]),
        )


@dataclass(frozen=True, slots=True)
class StrategyTradeCandidate:
    candidate_id: str
    strategy_name: str
    venue: str
    market: str
    underlying: str
    expiry: str
    legs: tuple[StrategyLeg, ...]
    entry_credit: float
    fair_value: float
    max_loss_per_contract: float
    target_debit: float
    stop_debit: float
    breakeven_low: float | None
    breakeven_high: float | None
    net_delta_per_contract: float
    net_gamma_per_contract: float
    net_vega_per_contract: float
    net_theta_per_contract: float
    metrics: CandidateMetrics
    score: float
    contracts: int = 1
    thesis: str = ""

    def with_contracts(self, contracts: int) -> "StrategyTradeCandidate":
        multiplier = max(int(contracts), 1)
        return replace(self, contracts=multiplier)

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "strategy_name": self.strategy_name,
            "venue": self.venue,
            "market": self.market,
            "underlying": self.underlying,
            "expiry": self.expiry,
            "legs": [leg.to_dict() for leg in self.legs],
            "entry_credit": self.entry_credit,
            "fair_value": self.fair_value,
            "max_loss_per_contract": self.max_loss_per_contract,
            "target_debit": self.target_debit,
            "stop_debit": self.stop_debit,
            "breakeven_low": self.breakeven_low,
            "breakeven_high": self.breakeven_high,
            "net_delta_per_contract": self.net_delta_per_contract,
            "net_gamma_per_contract": self.net_gamma_per_contract,
            "net_vega_per_contract": self.net_vega_per_contract,
            "net_theta_per_contract": self.net_theta_per_contract,
            "metrics": self.metrics.to_dict(),
            "score": self.score,
            "contracts": self.contracts,
            "thesis": self.thesis,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "StrategyTradeCandidate":
        return cls(
            candidate_id=str(payload["candidate_id"]),
            strategy_name=str(payload["strategy_name"]),
            venue=str(payload["venue"]),
            market=str(payload["market"]),
            underlying=str(payload["underlying"]),
            expiry=str(payload["expiry"]),
            legs=tuple(StrategyLeg.from_dict(item) for item in payload["legs"]),
            entry_credit=float(payload["entry_credit"]),
            fair_value=float(payload["fair_value"]),
            max_loss_per_contract=float(payload["max_loss_per_contract"]),
            target_debit=float(payload["target_debit"]),
            stop_debit=float(payload["stop_debit"]),
            breakeven_low=None if payload["breakeven_low"] is None else float(payload["breakeven_low"]),
            breakeven_high=None if payload["breakeven_high"] is None else float(payload["breakeven_high"]),
            net_delta_per_contract=float(payload["net_delta_per_contract"]),
            net_gamma_per_contract=float(payload["net_gamma_per_contract"]),
            net_vega_per_contract=float(payload["net_vega_per_contract"]),
            net_theta_per_contract=float(payload["net_theta_per_contract"]),
            metrics=CandidateMetrics.from_dict(payload["metrics"]),
            score=float(payload["score"]),
            contracts=int(payload.get("contracts", 1)),
            thesis=str(payload.get("thesis", "")),
        )


@dataclass(frozen=True, slots=True)
class ManagedPosition:
    position_id: str
    strategy_name: str
    venue: str
    mode: str
    market: str
    underlying: str
    expiry: str
    opened_at: str
    status: str
    contracts: int
    entry_credit: float
    target_debit: float
    stop_debit: float
    max_loss: float
    net_delta: float
    net_gamma: float
    net_vega: float
    net_theta: float
    legs: tuple[StrategyLeg, ...]
    candidate_id: str
    thesis: str
    last_mark: float = 0.0
    closed_at: str | None = None
    exit_reason: str | None = None
    realized_pnl: float | None = None
    order_payloads: tuple[dict[str, object], ...] = ()

    @classmethod
    def from_candidate(
        cls,
        candidate: StrategyTradeCandidate,
        *,
        mode: str,
        opened_at: str | None = None,
        order_payloads: tuple[dict[str, object], ...] = (),
    ) -> "ManagedPosition":
        timestamp = opened_at or datetime.now(UTC).isoformat()
        return cls(
            position_id=f"{candidate.candidate_id}:{timestamp}",
            strategy_name=candidate.strategy_name,
            venue=candidate.venue,
            mode=mode,
            market=candidate.market,
            underlying=candidate.underlying,
            expiry=candidate.expiry,
            opened_at=timestamp,
            status="OPEN",
            contracts=candidate.contracts,
            entry_credit=candidate.entry_credit,
            target_debit=candidate.target_debit,
            stop_debit=candidate.stop_debit,
            max_loss=candidate.max_loss_per_contract * candidate.contracts,
            net_delta=candidate.net_delta_per_contract * candidate.contracts,
            net_gamma=candidate.net_gamma_per_contract * candidate.contracts,
            net_vega=candidate.net_vega_per_contract * candidate.contracts,
            net_theta=candidate.net_theta_per_contract * candidate.contracts,
            legs=candidate.legs,
            candidate_id=candidate.candidate_id,
            thesis=candidate.thesis,
            order_payloads=order_payloads,
        )

    def close(self, *, closed_at: str, exit_reason: str, realized_pnl: float, last_mark: float) -> "ManagedPosition":
        return replace(
            self,
            status="CLOSED",
            closed_at=closed_at,
            exit_reason=exit_reason,
            realized_pnl=realized_pnl,
            last_mark=last_mark,
        )

    def with_mark(self, mark: float) -> "ManagedPosition":
        return replace(self, last_mark=mark)

    def to_dict(self) -> dict[str, object]:
        return {
            "position_id": self.position_id,
            "strategy_name": self.strategy_name,
            "venue": self.venue,
            "mode": self.mode,
            "market": self.market,
            "underlying": self.underlying,
            "expiry": self.expiry,
            "opened_at": self.opened_at,
            "status": self.status,
            "contracts": self.contracts,
            "entry_credit": self.entry_credit,
            "target_debit": self.target_debit,
            "stop_debit": self.stop_debit,
            "max_loss": self.max_loss,
            "net_delta": self.net_delta,
            "net_gamma": self.net_gamma,
            "net_vega": self.net_vega,
            "net_theta": self.net_theta,
            "legs": [leg.to_dict() for leg in self.legs],
            "candidate_id": self.candidate_id,
            "thesis": self.thesis,
            "last_mark": self.last_mark,
            "closed_at": self.closed_at,
            "exit_reason": self.exit_reason,
            "realized_pnl": self.realized_pnl,
            "order_payloads": [dict(item) for item in self.order_payloads],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ManagedPosition":
        return cls(
            position_id=str(payload["position_id"]),
            strategy_name=str(payload["strategy_name"]),
            venue=str(payload["venue"]),
            mode=str(payload["mode"]),
            market=str(payload["market"]),
            underlying=str(payload["underlying"]),
            expiry=str(payload["expiry"]),
            opened_at=str(payload["opened_at"]),
            status=str(payload["status"]),
            contracts=int(payload["contracts"]),
            entry_credit=float(payload["entry_credit"]),
            target_debit=float(payload["target_debit"]),
            stop_debit=float(payload["stop_debit"]),
            max_loss=float(payload["max_loss"]),
            net_delta=float(payload["net_delta"]),
            net_gamma=float(payload["net_gamma"]),
            net_vega=float(payload["net_vega"]),
            net_theta=float(payload["net_theta"]),
            legs=tuple(StrategyLeg.from_dict(item) for item in payload["legs"]),
            candidate_id=str(payload["candidate_id"]),
            thesis=str(payload["thesis"]),
            last_mark=float(payload.get("last_mark", 0.0)),
            closed_at=None if payload.get("closed_at") is None else str(payload["closed_at"]),
            exit_reason=None if payload.get("exit_reason") is None else str(payload["exit_reason"]),
            realized_pnl=None if payload.get("realized_pnl") is None else float(payload["realized_pnl"]),
            order_payloads=tuple(dict(item) for item in payload.get("order_payloads", [])),
        )
