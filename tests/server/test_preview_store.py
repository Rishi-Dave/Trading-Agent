"""In-memory preview→place binding (T2, ADR-0002: per-process, one-shot)."""

from __future__ import annotations

from etrade_agent.etrade.client import PreviewBinding
from etrade_agent.etrade.models import OrderAction, OrderPreview, OrderRequest, OrderType
from etrade_agent.server.preview_store import (
    _NO_PIPELINE_REASONING,
    PreviewStore,
    StoredPreview,
)


def _entry(preview_id: str = "abc") -> StoredPreview:
    order = OrderRequest(
        symbol="SPY", order_action=OrderAction.BUY, quantity=1, order_type=OrderType.MARKET
    )
    preview = OrderPreview(preview_id=preview_id, estimated_cost=100.0, warnings=[])
    binding = PreviewBinding(
        preview_ids=[{"previewId": preview_id}],
        order_type="EQ",
        order_block=[{}],
        client_order_id="clientorder1",
    )
    return StoredPreview(order=order, preview=preview, binding=binding)


def test_put_then_get_returns_the_stored_entry() -> None:
    store = PreviewStore()
    entry = _entry("abc")

    store.put(entry)

    assert store.get("abc") is entry


def test_get_unknown_preview_id_returns_none() -> None:
    store = PreviewStore()

    assert store.get("nope") is None


def test_consume_removes_entry_so_second_get_returns_none() -> None:
    store = PreviewStore()
    store.put(_entry("abc"))

    store.consume("abc")

    assert store.get("abc") is None


def test_consume_unknown_preview_id_is_a_noop() -> None:
    store = PreviewStore()

    store.consume("nope")  # must not raise


# --- ADR-0004: reasoning_summary/signals_json carried on the binding --------


def test_stored_preview_defaults_to_the_honest_no_pipeline_placeholder() -> None:
    entry = _entry("abc")

    assert entry.reasoning_summary == _NO_PIPELINE_REASONING
    assert entry.signals_json == "[]"


def test_stored_preview_can_carry_real_pipeline_reasoning() -> None:
    order = OrderRequest(
        symbol="SPY", order_action=OrderAction.BUY, quantity=1, order_type=OrderType.MARKET
    )
    preview = OrderPreview(preview_id="abc", estimated_cost=100.0, warnings=[])
    binding = PreviewBinding(
        preview_ids=[{"previewId": "abc"}],
        order_type="EQ",
        order_block=[{}],
        client_order_id="clientorder1",
    )

    entry = StoredPreview(
        order=order,
        preview=preview,
        binding=binding,
        reasoning_summary="real decision reasoning",
        signals_json='[{"source": "trader"}]',
    )

    assert entry.reasoning_summary == "real decision reasoning"
    assert entry.signals_json == '[{"source": "trader"}]'
