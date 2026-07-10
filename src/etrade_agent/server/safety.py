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


class PassthroughGate:
    """Phase-1-ONLY non-enforcing gate (ADR-0002). Enforces nothing — both checks
    always allow. Exists so the six tools (incl. preview_order/place_order) can be
    hand-tested against sandbox before Phase 2's real cap logic exists (SPEC §7
    Phase 1 deliverable). The call sites (T1) are wired in `server/tools.py`
    regardless — only the gate's *decision* is a no-op here.

    Safety net while this is in use: `server/app.py::create_app` hard-refuses to
    start outside `environment.mode == "sandbox"`, so this can never front a real
    order. Phase 2 replaces this with `ConfiguredSafetyGate` — the cap wall
    (tests/wall/) forces that swap before caps are considered live.
    """

    def check_preview(self, order: OrderRequest) -> Refusal | None:
        return None

    def check_place(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        return None


def preview_required_refusal(preview_id: str) -> Refusal:
    """Gate `preview-required` (SPEC §4.2, T2): `place_order` referenced a
    preview_id with no live binding in this run's PreviewStore. Authored here
    (not in the tool handler) so the refusal payload is defined alongside every
    other gate — the handler only does the dict lookup (server/tools.py)."""
    return Refusal(
        gate="preview-required",
        reason="place_order references no live preview from this run",
        state={"preview_id": preview_id},
    )
