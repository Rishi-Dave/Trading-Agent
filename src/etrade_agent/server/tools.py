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

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from etrade_agent import logs
from etrade_agent.config import AppConfig
from etrade_agent.etrade.client import EtradeClient
from etrade_agent.etrade.models import (
    OrderAction,
    OrderPreview,
    OrderRequest,
    OrderType,
    SecurityType,
)
from etrade_agent.server.preview_store import (
    _NO_PIPELINE_REASONING,
    PreviewStore,
    StoredPreview,
)
from etrade_agent.server.safety import Refusal, SafetyGate, preview_required_refusal
from etrade_agent.store.state import StateStore, today_utc

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
    client: EtradeClient,
    gate: SafetyGate,
    store: PreviewStore,
    state: StateStore,
    config: AppConfig,
    run_id: str,
    order: OrderRequest,
    reasoning_summary: str = _NO_PIPELINE_REASONING,
    signals_json: str = "[]",
) -> dict[str, Any]:
    """T1: the gate runs before any E*Trade call. `check_priced_preview` runs
    once pricing exists (capital-ceiling/per-trade-cap need estimated_cost,
    ADR-0003 point 7) and before the binding is stored, so an oversized order
    can never become placeable. On success, the binding is stored so a later
    place_order in this same run can reference it (T2). T4/SPEC §5.1: a
    refusal on a fully-specified order still gets a trade_log row — the
    JSONL refusal log (§4.1) isn't a substitute for the durable receipt.
    `reasoning_summary`/`signals_json` (ADR-0004) are the Decision's real
    T4 receipts when a pipeline called this; callers with no pipeline behind
    them (a direct/manual MCP call) get the honest default instead of a
    fabricated one."""
    refusal = gate.check_preview(order)
    if refusal is not None:
        _log_refusal(refusal)
        _write_refusal_receipt(
            state,
            config,
            run_id,
            order,
            preview=None,
            refusal=refusal,
            reasoning_summary=reasoning_summary,
            signals_json=signals_json,
        )
        return refusal.to_payload()

    preview, binding = client.preview_order(order)

    priced_refusal = gate.check_priced_preview(preview, order)
    if priced_refusal is not None:
        _log_refusal(priced_refusal)
        _write_refusal_receipt(
            state,
            config,
            run_id,
            order,
            preview=preview,
            refusal=priced_refusal,
            reasoning_summary=reasoning_summary,
            signals_json=signals_json,
        )
        return priced_refusal.to_payload()

    store.put(
        StoredPreview(
            order=order,
            preview=preview,
            binding=binding,
            reasoning_summary=reasoning_summary,
            signals_json=signals_json,
        )
    )
    return preview.model_dump(mode="json")


def _write_refusal_receipt(
    state: StateStore,
    config: AppConfig,
    run_id: str,
    order: OrderRequest,
    preview: OrderPreview | None,
    refusal: Refusal,
    *,
    reasoning_summary: str = _NO_PIPELINE_REASONING,
    signals_json: str = "[]",
) -> None:
    """T4/SPEC §5.1: trade_log is "one row per attempted order" — covers
    every refusal that has a real OrderRequest to attach (check_preview,
    check_priced_preview, check_place). The T2 preview-required refusal
    (server/tools.py::place_order, unknown preview_id) has no OrderRequest —
    store.get() found nothing — so there is no order data to write; it stays
    JSONL-only."""
    state.write_trade_log(
        run_id=run_id,
        config_version=config.config_version,
        symbol=order.symbol,
        order_action=order.order_action.value,
        security_type=order.security_type.value,
        quantity=order.quantity,
        preview_id=preview.preview_id if preview is not None else None,
        estimated_cost=preview.estimated_cost if preview is not None else None,
        executed=False,
        refusal_gate=refusal.gate,
        etrade_order_id=None,
        reasoning_summary=reasoning_summary,
        signals_json=signals_json,
        caps_snapshot_json=_caps_snapshot_json(config, state),
    )


def _caps_snapshot_json(config: AppConfig, state: StateStore) -> str:
    """T4: a snapshot of caps state at decision time, alongside the configured
    thresholds it was measured against — enough to reconstruct "why" without
    a second query."""
    day = today_utc()
    snapshot = state.read_caps_state(day)
    return json.dumps(
        {
            "date_utc": day,
            "trades_executed": snapshot.trades_executed,
            "realized_pnl": snapshot.realized_pnl,
            "breaker_tripped": snapshot.breaker_tripped,
            "per_trade_pct": config.caps.per_trade_pct,
            "daily_trade_limit": config.caps.daily_trade_limit,
            "daily_loss_pct": config.caps.daily_loss_pct,
            "pilot_amount_usd": config.capital.pilot_amount_usd,
        }
    )


def place_order(
    client: EtradeClient,
    gate: SafetyGate,
    store: PreviewStore,
    state: StateStore,
    config: AppConfig,
    run_id: str,
    preview_id: str,
) -> dict[str, Any]:
    """T2: place_order accepts only a preview_id bound to a preview issued in
    this run — the PreviewStore lookup IS that enforcement (an unknown id can
    never reach the E*Trade order endpoint). T1: check_place runs before the
    client call. One-shot: consumed only after a successful place. T4: a
    successful place writes a trade_log receipt (reasoning_summary/
    signals_json/caps_snapshot_json), inherited from the StoredPreview bound
    at preview_order time (ADR-0004) — the real Decision's reasoning when a
    pipeline supplied one, the honest default otherwise. Never blank."""
    entry = store.get(preview_id)
    if entry is None:
        missing_refusal = preview_required_refusal(preview_id)
        _log_refusal(missing_refusal)
        return missing_refusal.to_payload()

    refusal = gate.check_place(entry.preview, entry.order)
    if refusal is not None:
        _log_refusal(refusal)
        _write_refusal_receipt(
            state,
            config,
            run_id,
            entry.order,
            preview=entry.preview,
            refusal=refusal,
            reasoning_summary=entry.reasoning_summary,
            signals_json=entry.signals_json,
        )
        return refusal.to_payload()

    # Caps snapshot BEFORE this trade counts itself — "state at decision time."
    caps_snapshot_json = _caps_snapshot_json(config, state)

    status = client.place_from_binding(entry.binding)
    store.consume(preview_id)

    # The order has ALREADY executed at this point (irreversible, T2) — a
    # state-write failure here (e.g. concurrent access to trading.db from the
    # local CLIs/remote listener, ADR-0003 Consequences) must never be
    # silently swallowed, and must never make the caller think the order
    # itself failed (a retry on a false failure could double-place). Fail
    # LOUD instead: log at error level with everything needed to manually
    # backfill the trade_log row, and still return the real success.
    try:
        state.record_executed_trade(
            date_utc=today_utc(),
            run_id=run_id,
            config_version=config.config_version,
            symbol=entry.order.symbol,
            order_action=entry.order.order_action.value,
            security_type=entry.order.security_type.value,
            quantity=entry.order.quantity,
            preview_id=entry.preview.preview_id,
            estimated_cost=entry.preview.estimated_cost,
            executed=True,
            refusal_gate=None,
            etrade_order_id=status.etrade_order_id,
            reasoning_summary=entry.reasoning_summary,
            signals_json=entry.signals_json,
            caps_snapshot_json=caps_snapshot_json,
        )
    except Exception as exc:
        logs.log(
            _AGENT_ID,
            "error",
            "EXECUTED TRADE FAILED TO WRITE trade_log / increment daily count "
            "— manual backfill required (T4)",
            run_id=run_id,
            symbol=entry.order.symbol,
            order_action=entry.order.order_action.value,
            security_type=entry.order.security_type.value,
            quantity=entry.order.quantity,
            preview_id=entry.preview.preview_id,
            estimated_cost=entry.preview.estimated_cost,
            etrade_order_id=status.etrade_order_id,
            error=str(exc),
        )

    return status.model_dump(mode="json")


def register_tools(
    app: FastMCP,
    client: EtradeClient,
    gate: SafetyGate,
    store: PreviewStore,
    state: StateStore,
    config: AppConfig,
    run_id: str,
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
        reasoning_summary: str = _NO_PIPELINE_REASONING,
        signals_json: str = "[]",
    ) -> dict[str, Any]:
        """Preview an order (T2: place_order requires this preview's id).
        Refusal shape (SPEC §4.1): {"refused": true, "gate", "reason", "state"}.
        `reasoning_summary`/`signals_json` (ADR-0004): a decision pipeline's
        real T4 receipts for this order, if it has one — left at the honest
        default for a direct/manual call."""
        order = OrderRequest(
            symbol=symbol,
            order_action=OrderAction(order_action),
            quantity=quantity,
            security_type=SecurityType(security_type),
            order_type=OrderType(order_type),
            limit_price=limit_price,
        )
        return preview_order(
            client,
            gate,
            store,
            state,
            config,
            run_id,
            order,
            reasoning_summary=reasoning_summary,
            signals_json=signals_json,
        )

    @app.tool(name="place_order")
    def _place_order(preview_id: str) -> dict[str, Any]:
        """Place an order previously previewed in this run (T2). Refusal shape
        (SPEC §4.1): {"refused": true, "gate", "reason", "state"}."""
        return place_order(client, gate, store, state, config, run_id, preview_id)
