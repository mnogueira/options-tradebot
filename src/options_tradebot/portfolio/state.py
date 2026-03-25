"""Persistent portfolio state for managed defined-risk positions."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from options_tradebot.strategies.defined_risk.types import ManagedPosition


@dataclass(frozen=True, slots=True)
class PortfolioState:
    open_positions: tuple[ManagedPosition, ...] = ()
    closed_positions: tuple[ManagedPosition, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "open_positions": [position.to_dict() for position in self.open_positions],
            "closed_positions": [position.to_dict() for position in self.closed_positions],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "PortfolioState":
        return cls(
            open_positions=tuple(ManagedPosition.from_dict(item) for item in payload.get("open_positions", [])),
            closed_positions=tuple(ManagedPosition.from_dict(item) for item in payload.get("closed_positions", [])),
        )


def load_portfolio_state(path: str | Path) -> PortfolioState:
    target = Path(path)
    if not target.exists():
        return PortfolioState()
    payload = json.loads(target.read_text(encoding="utf-8"))
    return PortfolioState.from_dict(payload)


def save_portfolio_state(path: str | Path, state: PortfolioState) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    return target
