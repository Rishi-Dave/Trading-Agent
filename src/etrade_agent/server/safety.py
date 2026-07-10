"""Safety layer: the ONLY enforcement point for caps/whitelist/breaker/kill switch (T1).

Every gate in SPEC §4.2 becomes a check here in Phase 2; each maps 1:1 to a wall
test in tests/wall/. Gates evaluate the preview result (T2), fail closed, and
refuse with the exact SPEC §4.1 payload shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from etrade_agent.config import AppConfig
from etrade_agent.etrade.models import OrderPreview, OrderRequest


@dataclass(frozen=True)
class Refusal:
    """SPEC §4.1 refusal payload. This shape is a parsed contract — do not drift it."""

    gate: str
    reason: str
    state: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {"refused": True, "gate": self.gate, "reason": self.reason, "state": self.state}


class SafetyGate(Protocol):
    """Checked by tool handlers BEFORE any E*Trade call (SPEC §4.2)."""

    def check_preview(self, order: OrderRequest) -> Refusal | None:
        """Preview-time gates: capital-ceiling, per-trade-cap, whitelist, policy-*."""
        ...

    def check_place(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        """All gates, kill-switch first (SPEC §4.2). None means the order may proceed."""
        ...


class ConfiguredSafetyGate:
    """Phase 2 implementation target (SPEC §7). Construction requires valid caps (T5)."""

    def __init__(self, config: AppConfig) -> None:
        # AppConfig cannot be constructed without caps (T5); keeping the whole config
        # here means every gate reads the same validated snapshot.
        self._config = config

    def check_preview(self, order: OrderRequest) -> Refusal | None:
        raise NotImplementedError("Phase 2 (SPEC §7)")

    def check_place(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        raise NotImplementedError("Phase 2 (SPEC §7)")
