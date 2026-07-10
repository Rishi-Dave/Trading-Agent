"""Six MCP tool handlers (SPEC §5.2). T1: gate checked before any E*Trade call.
T2: place_order only executes a preview issued this run.

Business logic is tested as plain functions (no FastMCP protocol machinery) —
register_tools' FastMCP wiring itself gets one light integration check plus the
Task #10 hand-test against real sandbox via .mcp.json.

FastMCP's ToolError wraps/prefixes any raised message (verified live against
the installed mcp package: "Error executing tool X: <original message>"), which
would corrupt the SPEC §4.1 payload shape. Refusals are therefore returned as
the tool's normal result — never raised — so the exact {"refused": true, ...}
shape survives (server/CLAUDE.md: "a parsed contract, not a message")."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from etrade_agent.etrade.client import PreviewBinding
from etrade_agent.etrade.models import (
    Balance,
    OrderAction,
    OrderPreview,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    Quote,
)
from etrade_agent.server import tools
from etrade_agent.server.preview_store import PreviewStore, StoredPreview
from etrade_agent.server.safety import Refusal


@dataclass
class SpyGate:
    """Records call order; returns queued refusals (None = allow)."""

    preview_refusal: Refusal | None = None
    place_refusal: Refusal | None = None
    calls: list[str] = field(default_factory=list)

    def check_preview(self, order: OrderRequest) -> Refusal | None:
        self.calls.append("check_preview")
        return self.preview_refusal

    def check_place(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        self.calls.append("check_place")
        return self.place_refusal


@dataclass
class SpyClient:
    """Fake EtradeClient; records call order alongside the gate's SpyGate.calls
    list (both append to the SAME shared list) to prove ordering (T1)."""

    calls: list[str]
    preview_result: tuple[OrderPreview, PreviewBinding] | None = None
    place_result: OrderStatus | None = None

    def get_quote(self, symbol: str) -> Quote:
        self.calls.append("get_quote")
        return Quote(
            symbol=symbol, bid=1, ask=2, last=1.5, volume=100, as_of="2026-01-01T00:00:00Z"
        )

    def get_positions(self) -> list[Position]:
        self.calls.append("get_positions")
        return [Position(symbol="AAPL", quantity=1, cost_basis=100, market_value=110)]

    def get_balances(self) -> Balance:
        self.calls.append("get_balances")
        return Balance(account_value=1, cash_available=1, buying_power=1)

    def get_order_status(self, etrade_order_id: str) -> OrderStatus:
        self.calls.append("get_order_status")
        return OrderStatus(etrade_order_id=etrade_order_id, status="OPEN", filled_quantity=0)

    def preview_order(self, order: OrderRequest) -> tuple[OrderPreview, PreviewBinding]:
        self.calls.append("client.preview_order")
        assert self.preview_result is not None
        return self.preview_result

    def place_from_binding(self, binding: PreviewBinding) -> OrderStatus:
        self.calls.append("client.place_from_binding")
        assert self.place_result is not None
        return self.place_result


def _order() -> OrderRequest:
    return OrderRequest(
        symbol="SPY", order_action=OrderAction.BUY, quantity=1, order_type=OrderType.MARKET
    )


def _preview_and_binding(preview_id: str = "pv1") -> tuple[OrderPreview, PreviewBinding]:
    preview = OrderPreview(preview_id=preview_id, estimated_cost=100.0, warnings=[])
    binding = PreviewBinding(
        preview_ids=[{"previewId": preview_id}],
        order_type="EQ",
        order_block=[{}],
        client_order_id="clientorder1",
    )
    return preview, binding


# --- read tools: thin passthrough, no gate involvement ---


def test_get_quote_returns_model_dict() -> None:
    client = SpyClient(calls=[])

    result = tools.get_quote(client, "SPY")

    assert result["symbol"] == "SPY"


def test_get_positions_returns_list_of_dicts() -> None:
    client = SpyClient(calls=[])

    result = tools.get_positions(client)

    assert result == [
        {"symbol": "AAPL", "quantity": 1.0, "cost_basis": 100.0, "market_value": 110.0}
    ]


def test_get_balances_returns_model_dict() -> None:
    client = SpyClient(calls=[])

    result = tools.get_balances(client)

    assert result["account_value"] == 1.0


def test_get_order_status_returns_model_dict() -> None:
    client = SpyClient(calls=[])

    result = tools.get_order_status(client, "42")

    assert result["etrade_order_id"] == "42"


# --- preview_order: T1 (gate before client), stores binding on success ---


def test_preview_order_calls_gate_before_client() -> None:
    calls: list[str] = []
    client = SpyClient(calls=calls, preview_result=_preview_and_binding())
    gate = SpyGate()
    gate.calls = calls  # share the list so ordering across gate+client is provable
    store = PreviewStore()

    tools.preview_order(client, gate, store, _order())

    assert calls == ["check_preview", "client.preview_order"]


def test_preview_order_refusal_returns_payload_without_calling_client() -> None:
    client = SpyClient(calls=[])
    refusal = Refusal(gate="whitelist", reason="not whitelisted", state={"symbol": "SPY"})
    gate = SpyGate(preview_refusal=refusal)
    store = PreviewStore()

    result = tools.preview_order(client, gate, store, _order())

    assert result == refusal.to_payload()
    assert client.calls == []  # T1: no E*Trade call after a refusal


def test_preview_order_success_stores_binding_for_later_place() -> None:
    preview, binding = _preview_and_binding("pv-xyz")
    client = SpyClient(calls=[], preview_result=(preview, binding))
    gate = SpyGate()
    store = PreviewStore()

    result = tools.preview_order(client, gate, store, _order())

    assert result["preview_id"] == "pv-xyz"
    stored = store.get("pv-xyz")
    assert stored is not None
    assert stored.binding == binding


# --- place_order: T2 (preview must exist), T1 (gate before client) ---


def test_place_order_unknown_preview_id_refuses_without_calling_client() -> None:
    client = SpyClient(calls=[])
    gate = SpyGate()
    store = PreviewStore()

    result = tools.place_order(client, gate, store, "nonexistent")

    assert result["refused"] is True
    assert result["gate"] == "preview-required"
    assert client.calls == []
    assert gate.calls == []  # T2 check happens before T1's check_place is even reached


def test_place_order_calls_gate_before_client_place() -> None:
    calls: list[str] = []
    preview, binding = _preview_and_binding("pv1")
    client = SpyClient(
        calls=calls,
        place_result=OrderStatus(etrade_order_id="999", status="OPEN", filled_quantity=0),
    )
    gate = SpyGate()
    gate.calls = calls
    store = PreviewStore()
    store.put(StoredPreview(order=_order(), preview=preview, binding=binding))

    tools.place_order(client, gate, store, "pv1")

    assert calls == ["check_place", "client.place_from_binding"]


def test_place_order_refusal_does_not_consume_preview_or_call_client() -> None:
    preview, binding = _preview_and_binding("pv1")
    client = SpyClient(calls=[])
    refusal = Refusal(gate="kill-switch", reason="engaged", state={})
    gate = SpyGate(place_refusal=refusal)
    store = PreviewStore()
    store.put(StoredPreview(order=_order(), preview=preview, binding=binding))

    result = tools.place_order(client, gate, store, "pv1")

    assert result == refusal.to_payload()
    assert client.calls == []
    assert store.get("pv1") is not None  # not consumed — refused, not placed


def test_place_order_success_consumes_the_preview() -> None:
    preview, binding = _preview_and_binding("pv1")
    client = SpyClient(
        calls=[],
        place_result=OrderStatus(etrade_order_id="999", status="OPEN", filled_quantity=0),
    )
    gate = SpyGate()
    store = PreviewStore()
    store.put(StoredPreview(order=_order(), preview=preview, binding=binding))

    result = tools.place_order(client, gate, store, "pv1")

    assert result["etrade_order_id"] == "999"
    assert store.get("pv1") is None  # one-shot: consumed after placing


def test_place_order_second_call_with_same_preview_id_refuses() -> None:
    preview, binding = _preview_and_binding("pv1")
    client = SpyClient(
        calls=[],
        place_result=OrderStatus(etrade_order_id="999", status="OPEN", filled_quantity=0),
    )
    gate = SpyGate()
    store = PreviewStore()
    store.put(StoredPreview(order=_order(), preview=preview, binding=binding))
    tools.place_order(client, gate, store, "pv1")  # first call consumes it

    result = tools.place_order(client, gate, store, "pv1")  # second: replay attempt

    assert result["refused"] is True
    assert result["gate"] == "preview-required"


# --- register_tools: light FastMCP integration check ---


def test_register_tools_registers_all_six_tool_names() -> None:
    from mcp.server.fastmcp import FastMCP

    app = FastMCP("test-etrade")
    client = SpyClient(calls=[])
    gate = SpyGate()
    store = PreviewStore()

    tools.register_tools(app, client, gate, store)  # type: ignore[arg-type]

    registered = {t.name for t in asyncio.run(app.list_tools())}
    assert registered == {
        "get_quote",
        "get_positions",
        "get_balances",
        "preview_order",
        "place_order",
        "get_order_status",
    }
