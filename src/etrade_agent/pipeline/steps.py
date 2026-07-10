"""Shape-agnostic pipeline contracts (SPEC §6) — frozen while the shape is undecided.

The Phase 3 spike (TradingAgents vs AI Hedge Fund, ADR required) decides the role
graph; until then everything composes through these protocols. The pipeline
PROPOSES; the server DISPOSES (T1) — nothing here enforces anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class Action(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Signal:
    """One dated piece of evidence a decision rests on — becomes a T4 receipt."""

    source: str
    as_of: datetime
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    """Pipeline output. reasoning_summary + signals flow into trade_log (T4)."""

    action: Action
    symbol: str
    quantity: int
    confidence: float
    reasoning_summary: str
    signals: tuple[Signal, ...]


@dataclass
class PipelineContext:
    """Mutable bag passed step to step; each role reads and annotates it."""

    run_id: str
    symbols: list[str]
    signals: list[Signal] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)
    decisions: list[Decision] = field(default_factory=list)


class PipelineStep(Protocol):
    """A role (analyst, aggregator, trader, advisory risk check) is just a step."""

    name: str

    def run(self, context: PipelineContext) -> PipelineContext: ...
