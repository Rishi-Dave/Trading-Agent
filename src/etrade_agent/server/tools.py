"""MCP tool handlers (SPEC §5.2), implemented in Phase 1.

Every order-mutating handler calls the SafetyGate before any E*Trade call (T1);
place_order accepts only a preview_id resolved against the in-process
PreviewStore (T2). Registration happens in app.py.

Refusals are returned as the tool's normal result — `refusal.to_payload()`,
the exact SPEC §4.1 `{"refused": true, ...}` shape — never raised. Raising
FastMCP's ToolError wraps/prefixes the message ("Error executing tool X: ...",
verified against the installed mcp package), which would corrupt the payload
into free text; server/CLAUDE.md is explicit that the refusal shape is "a
parsed contract, not a message."

Business logic lives in plain functions (`get_quote`, `preview_order`, etc.)
that take their dependencies as arguments — testable with no FastMCP protocol
machinery involved. `register_tools` is thin FastMCP wiring around them.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from etrade_agent import logs
from etrade_agent.etrade.client import EtradeClient
from etrade_agent.etrade.models import (
    OrderAction,
    OrderRequest,
    OrderType,
    SecurityType,
)
from etrade_agent.server.preview_store import PreviewStore, StoredPreview
from etrade_agent.server.safety import Refusal, SafetyGate, preview_required_refusal

_AGENT_ID = "etrade-server"


def _log_refusal(refusal: Refusal) -> None:
    """SPEC §4.1: refusals are logged (JSONL, level=warning)."""
    logs.log(_AGENT_ID, "warning", "order-mutating tool call refused", **refusal.to_payload())


def get_quote(client: EtradeClient, symbol: str) -> dict[str, Any]:
    return client.get_quote(symbol).model_dump(mode="json")


def get_positions(client: EtradeClient) -> list[dict[str, Any]]:
    return [p.model_dump(mode="json") for p in client.get_positions()]


def get_balances(client: EtradeClient) -> dict[str, Any]:
    return client.get_balances().model_dump(mode="json")


def get_order_status(client: EtradeClient, etrade_order_id: str) -> dict[str, Any]:
    return client.get_order_status(etrade_order_id).model_dump(mode="json")


def preview_order(
    client: EtradeClient, gate: SafetyGate, store: PreviewStore, order: OrderRequest
) -> dict[str, Any]:
    """T1: the gate runs before any E*Trade call. On success, the binding is
    stored so a later place_order in this same run can reference it (T2)."""
    refusal = gate.check_preview(order)
    if refusal is not None:
        _log_refusal(refusal)
        return refusal.to_payload()

    preview, binding = client.preview_order(order)
    store.put(StoredPreview(order=order, preview=preview, binding=binding))
    return preview.model_dump(mode="json")


def place_order(
    client: EtradeClient, gate: SafetyGate, store: PreviewStore, preview_id: str
) -> dict[str, Any]:
    """T2: place_order accepts only a preview_id bound to a preview issued in
    this run — the PreviewStore lookup IS that enforcement (an unknown id can
    never reach the E*Trade order endpoint). T1: check_place runs before the
    client call. One-shot: consumed only after a successful place."""
    entry = store.get(preview_id)
    if entry is None:
        missing_refusal = preview_required_refusal(preview_id)
        _log_refusal(missing_refusal)
        return missing_refusal.to_payload()

    refusal = gate.check_place(entry.preview, entry.order)
    if refusal is not None:
        _log_refusal(refusal)
        return refusal.to_payload()

    status = client.place_from_binding(entry.binding)
    store.consume(preview_id)
    return status.model_dump(mode="json")


def register_tools(
    app: FastMCP, client: EtradeClient, gate: SafetyGate, store: PreviewStore
) -> None:
    """Register the six SPEC §5.2 tools on the FastMCP app."""

    @app.tool(name="get_quote")
    def _get_quote(symbol: str) -> dict[str, Any]:
        """Get a live quote for `symbol`."""
        return get_quote(client, symbol)

    @app.tool(name="get_positions")
    def _get_positions() -> list[dict[str, Any]]:
        """List current account positions."""
        return get_positions(client)

    @app.tool(name="get_balances")
    def _get_balances() -> dict[str, Any]:
        """Get current account balances."""
        return get_balances(client)

    @app.tool(name="get_order_status")
    def _get_order_status(etrade_order_id: str) -> dict[str, Any]:
        """Get the status of a previously placed order."""
        return get_order_status(client, etrade_order_id)

    @app.tool(name="preview_order")
    def _preview_order(
        symbol: str,
        order_action: str,
        quantity: int,
        order_type: str,
        security_type: str = "EQ",
        limit_price: float | None = None,
    ) -> dict[str, Any]:
        """Preview an order (T2: place_order requires this preview's id).
        Refusal shape (SPEC §4.1): {"refused": true, "gate", "reason", "state"}.
        """
        order = OrderRequest(
            symbol=symbol,
            order_action=OrderAction(order_action),
            quantity=quantity,
            security_type=SecurityType(security_type),
            order_type=OrderType(order_type),
            limit_price=limit_price,
        )
        return preview_order(client, gate, store, order)

    @app.tool(name="place_order")
    def _place_order(preview_id: str) -> dict[str, Any]:
        """Place an order previously previewed in this run (T2). Refusal shape
        (SPEC §4.1): {"refused": true, "gate", "reason", "state"}."""
        return place_order(client, gate, store, preview_id)
