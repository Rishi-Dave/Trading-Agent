"""Phase 1 safety-layer additions (SPEC §4, T1/T2). Gate LOGIC is Phase 2 —
these are the Phase-1 wiring pieces: a non-enforcing PassthroughGate (labeled,
sandbox-only, swapped for ConfiguredSafetyGate in Phase 2) and the T2
missing-preview refusal factory, authored here per T1 (enforcement lives in
safety.py, not in the tool handler)."""

from __future__ import annotations

from etrade_agent.etrade.models import OrderAction, OrderPreview, OrderRequest, OrderType
from etrade_agent.server.safety import PassthroughGate, preview_required_refusal


def _order() -> OrderRequest:
    return OrderRequest(
        symbol="SPY", order_action=OrderAction.BUY, quantity=1, order_type=OrderType.MARKET
    )


def test_passthrough_gate_check_preview_always_allows() -> None:
    gate = PassthroughGate()

    assert gate.check_preview(_order()) is None


def test_passthrough_gate_check_place_always_allows() -> None:
    gate = PassthroughGate()
    preview = OrderPreview(preview_id="1", estimated_cost=100.0, warnings=[])

    assert gate.check_place(preview, _order()) is None


def test_preview_required_refusal_shape() -> None:
    refusal = preview_required_refusal("missing-id-123")

    payload = refusal.to_payload()
    assert payload["refused"] is True
    assert payload["gate"] == "preview-required"
    assert "preview" in payload["reason"].lower()
    assert payload["state"]["preview_id"] == "missing-id-123"
