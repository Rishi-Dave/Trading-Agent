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
from pathlib import Path

import pytest

from etrade_agent.config import AppConfig, load_config
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
from etrade_agent.store import db
from etrade_agent.store.state import StateStore, today_utc
from tests.conftest import VALID_CONFIG_TOML


@dataclass
class SpyGate:
    """Records call order; returns queued refusals (None = allow)."""

    preview_refusal: Refusal | None = None
    priced_preview_refusal: Refusal | None = None
    place_refusal: Refusal | None = None
    calls: list[str] = field(default_factory=list)

    def check_preview(self, order: OrderRequest) -> Refusal | None:
        self.calls.append("check_preview")
        return self.preview_refusal

    def check_priced_preview(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        self.calls.append("check_priced_preview")
        return self.priced_preview_refusal

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


def _config(tmp_path: Path) -> AppConfig:
    path = tmp_path / "config.toml"
    path.write_text(VALID_CONFIG_TOML)
    return load_config(path)


def _state(tmp_path: Path) -> StateStore:
    conn = db.connect(tmp_path / "trading.db")
    return StateStore(conn)


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


def test_preview_order_calls_gate_before_client(tmp_path: Path) -> None:
    calls: list[str] = []
    client = SpyClient(calls=calls, preview_result=_preview_and_binding())
    gate = SpyGate()
    gate.calls = calls  # share the list so ordering across gate+client is provable
    store = PreviewStore()

    tools.preview_order(client, gate, store, _state(tmp_path), _config(tmp_path), _RUN_ID, _order())

    # check_priced_preview runs AFTER pricing (needs estimated_cost) and BEFORE
    # the binding is stored (ADR-0003 point 7).
    assert calls == ["check_preview", "client.preview_order", "check_priced_preview"]


def test_preview_order_refusal_returns_payload_without_calling_client(tmp_path: Path) -> None:
    client = SpyClient(calls=[])
    refusal = Refusal(gate="whitelist", reason="not whitelisted", state={"symbol": "SPY"})
    gate = SpyGate(preview_refusal=refusal)
    store = PreviewStore()

    result = tools.preview_order(
        client, gate, store, _state(tmp_path), _config(tmp_path), _RUN_ID, _order()
    )

    assert result == refusal.to_payload()
    assert client.calls == []  # T1: no E*Trade call after a refusal


def test_preview_order_refusal_writes_trade_log_row(tmp_path: Path) -> None:
    """T4/SPEC §5.1: trade_log is "one row per attempted order" — a
    check_preview refusal has a full OrderRequest (just no preview/estimated
    cost yet), so it gets a row too, not just the JSONL refusal log."""
    client = SpyClient(calls=[])
    refusal = Refusal(gate="whitelist", reason="not whitelisted", state={"symbol": "SPY"})
    gate = SpyGate(preview_refusal=refusal)
    store = PreviewStore()
    state = _state(tmp_path)

    tools.preview_order(client, gate, store, state, _config(tmp_path), _RUN_ID, _order())

    row = state.conn.execute(
        "SELECT executed, refusal_gate, preview_id, estimated_cost, symbol FROM trade_log"
    ).fetchone()
    assert row is not None
    assert row[0] == 0
    assert row[1] == "whitelist"
    assert row[2] is None  # no preview existed yet at check_preview time
    assert row[3] is None
    assert row[4] == "SPY"


def test_preview_order_priced_refusal_returns_payload_without_storing(tmp_path: Path) -> None:
    """capital-ceiling/per-trade-cap: refused only once pricing exists
    (ADR-0003 point 7) — the binding must never be stored, so a too-big order
    can never become placeable even indirectly."""
    preview, binding = _preview_and_binding("pv-oversized")
    client = SpyClient(calls=[], preview_result=(preview, binding))
    refusal = Refusal(gate="per-trade-cap", reason="too big", state={"estimated_cost": 100.0})
    gate = SpyGate(priced_preview_refusal=refusal)
    store = PreviewStore()

    result = tools.preview_order(
        client, gate, store, _state(tmp_path), _config(tmp_path), _RUN_ID, _order()
    )

    assert result == refusal.to_payload()
    assert store.get("pv-oversized") is None


def test_preview_order_priced_refusal_writes_trade_log_row_with_preview_data(
    tmp_path: Path,
) -> None:
    """Same as check_preview's refusal receipt, but pricing already happened
    — the row carries the real preview_id/estimated_cost."""
    preview, binding = _preview_and_binding("pv-oversized")
    client = SpyClient(calls=[], preview_result=(preview, binding))
    refusal = Refusal(gate="per-trade-cap", reason="too big", state={"estimated_cost": 100.0})
    gate = SpyGate(priced_preview_refusal=refusal)
    store = PreviewStore()
    state = _state(tmp_path)

    tools.preview_order(client, gate, store, state, _config(tmp_path), _RUN_ID, _order())

    row = state.conn.execute(
        "SELECT executed, refusal_gate, preview_id, estimated_cost FROM trade_log"
    ).fetchone()
    assert row is not None
    assert row[0] == 0
    assert row[1] == "per-trade-cap"
    assert row[2] == "pv-oversized"
    assert row[3] == 100.0


def test_preview_order_success_stores_binding_for_later_place(tmp_path: Path) -> None:
    preview, binding = _preview_and_binding("pv-xyz")
    client = SpyClient(calls=[], preview_result=(preview, binding))
    gate = SpyGate()
    store = PreviewStore()

    result = tools.preview_order(
        client, gate, store, _state(tmp_path), _config(tmp_path), _RUN_ID, _order()
    )

    assert result["preview_id"] == "pv-xyz"
    stored = store.get("pv-xyz")
    assert stored is not None
    assert stored.binding == binding


# --- ADR-0004: reasoning_summary/signals_json passthrough (T4 receipt seam) --


def test_preview_order_default_reasoning_is_the_honest_placeholder(tmp_path: Path) -> None:
    """A caller with no pipeline behind it (direct/manual MCP call) gets the
    honest default, not a fabricated claim that a pipeline ran."""
    preview, binding = _preview_and_binding("pv-default")
    client = SpyClient(calls=[], preview_result=(preview, binding))
    gate = SpyGate()
    store = PreviewStore()

    tools.preview_order(client, gate, store, _state(tmp_path), _config(tmp_path), _RUN_ID, _order())

    stored = store.get("pv-default")
    assert stored is not None
    assert stored.reasoning_summary == tools._NO_PIPELINE_REASONING
    assert stored.signals_json == "[]"


def test_preview_order_stores_supplied_reasoning_and_signals(tmp_path: Path) -> None:
    """ADR-0004: a Decision's real reasoning, when a pipeline supplied one,
    is bound to the StoredPreview so place_order inherits it (T2-aligned:
    no new place-time parameter)."""
    preview, binding = _preview_and_binding("pv-real")
    client = SpyClient(calls=[], preview_result=(preview, binding))
    gate = SpyGate()
    store = PreviewStore()
    real_signals_json = (
        '[{"source": "trader", "as_of": "2026-07-11T00:00:00+00:00", "summary": "x", "detail": {}}]'
    )

    tools.preview_order(
        client,
        gate,
        store,
        _state(tmp_path),
        _config(tmp_path),
        _RUN_ID,
        _order(),
        reasoning_summary="real decision reasoning",
        signals_json=real_signals_json,
    )

    stored = store.get("pv-real")
    assert stored is not None
    assert stored.reasoning_summary == "real decision reasoning"
    assert stored.signals_json == real_signals_json


def test_preview_order_refusal_writes_supplied_reasoning_not_the_placeholder(
    tmp_path: Path,
) -> None:
    client = SpyClient(calls=[])
    refusal = Refusal(gate="whitelist", reason="not whitelisted", state={"symbol": "SPY"})
    gate = SpyGate(preview_refusal=refusal)
    store = PreviewStore()
    state = _state(tmp_path)

    tools.preview_order(
        client,
        gate,
        store,
        state,
        _config(tmp_path),
        _RUN_ID,
        _order(),
        reasoning_summary="real decision reasoning",
        signals_json='[{"source": "trader"}]',
    )

    row = state.conn.execute("SELECT reasoning_summary, signals_json FROM trade_log").fetchone()
    assert row is not None
    assert row[0] == "real decision reasoning"
    assert row[0] != tools._NO_PIPELINE_REASONING
    assert row[1] == '[{"source": "trader"}]'


# --- place_order: T2 (preview must exist), T1 (gate before client) ---

_RUN_ID = "test-run-1"


def test_place_order_unknown_preview_id_refuses_without_calling_client(tmp_path: Path) -> None:
    client = SpyClient(calls=[])
    gate = SpyGate()
    store = PreviewStore()

    result = tools.place_order(
        client, gate, store, _state(tmp_path), _config(tmp_path), _RUN_ID, "nonexistent"
    )

    assert result["refused"] is True
    assert result["gate"] == "preview-required"
    assert client.calls == []
    assert gate.calls == []  # T2 check happens before T1's check_place is even reached


def test_place_order_calls_gate_before_client_place(tmp_path: Path) -> None:
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

    tools.place_order(client, gate, store, _state(tmp_path), _config(tmp_path), _RUN_ID, "pv1")

    assert calls == ["check_place", "client.place_from_binding"]


def test_place_order_refusal_does_not_consume_preview_or_call_client(tmp_path: Path) -> None:
    preview, binding = _preview_and_binding("pv1")
    client = SpyClient(calls=[])
    refusal = Refusal(gate="kill-switch", reason="engaged", state={})
    gate = SpyGate(place_refusal=refusal)
    store = PreviewStore()
    store.put(StoredPreview(order=_order(), preview=preview, binding=binding))

    result = tools.place_order(
        client, gate, store, _state(tmp_path), _config(tmp_path), _RUN_ID, "pv1"
    )

    assert result == refusal.to_payload()
    assert client.calls == []
    assert store.get("pv1") is not None  # not consumed — refused, not placed


def test_place_order_refusal_writes_trade_log_row_but_does_not_increment(
    tmp_path: Path,
) -> None:
    """T4/SPEC §5.1: a check_place refusal has a full OrderRequest + priced
    OrderPreview available (the T2 binding), so it gets a trade_log row too
    — already logged via JSONL (§4.1) doesn't substitute for the durable
    receipt. It must NOT count toward daily_trade_limit though: a refused
    attempt never executed, so the daily count stays untouched."""
    preview, binding = _preview_and_binding("pv1")
    client = SpyClient(calls=[])
    gate = SpyGate(place_refusal=Refusal(gate="kill-switch", reason="engaged", state={}))
    store = PreviewStore()
    store.put(StoredPreview(order=_order(), preview=preview, binding=binding))
    state = _state(tmp_path)

    tools.place_order(client, gate, store, state, _config(tmp_path), _RUN_ID, "pv1")

    row = state.conn.execute(
        "SELECT executed, refusal_gate, preview_id, estimated_cost, etrade_order_id FROM trade_log"
    ).fetchone()
    assert row is not None
    assert row[0] == 0
    assert row[1] == "kill-switch"
    assert row[2] == "pv1"
    assert row[3] == 100.0
    assert row[4] is None
    assert state.read_caps_state(today_utc()).trades_executed == 0


def test_place_order_success_consumes_the_preview(tmp_path: Path) -> None:
    preview, binding = _preview_and_binding("pv1")
    client = SpyClient(
        calls=[],
        place_result=OrderStatus(etrade_order_id="999", status="OPEN", filled_quantity=0),
    )
    gate = SpyGate()
    store = PreviewStore()
    store.put(StoredPreview(order=_order(), preview=preview, binding=binding))

    result = tools.place_order(
        client, gate, store, _state(tmp_path), _config(tmp_path), _RUN_ID, "pv1"
    )

    assert result["etrade_order_id"] == "999"
    assert store.get("pv1") is None  # one-shot: consumed after placing


def test_place_order_success_writes_t4_trade_log_receipt(tmp_path: Path) -> None:
    """T4: every executed trade carries reasoning receipts. Phase 2 has no real
    pipeline yet (Phase 3) — a placeholder reasoning_summary is fine, an
    empty/missing column is not."""
    preview, binding = _preview_and_binding("pv1")
    client = SpyClient(
        calls=[],
        place_result=OrderStatus(etrade_order_id="999", status="OPEN", filled_quantity=0),
    )
    gate = SpyGate()
    store = PreviewStore()
    store.put(StoredPreview(order=_order(), preview=preview, binding=binding))
    state = _state(tmp_path)
    config = _config(tmp_path)

    tools.place_order(client, gate, store, state, config, _RUN_ID, "pv1")

    row = state.conn.execute(
        "SELECT run_id, config_version, symbol, order_action, security_type, quantity, "
        "preview_id, estimated_cost, executed, refusal_gate, etrade_order_id, "
        "reasoning_summary, signals_json, caps_snapshot_json FROM trade_log"
    ).fetchone()
    assert row is not None
    (
        run_id,
        config_version,
        symbol,
        order_action,
        security_type,
        quantity,
        preview_id,
        estimated_cost,
        executed,
        refusal_gate,
        etrade_order_id,
        reasoning_summary,
        signals_json,
        caps_snapshot_json,
    ) = row
    assert run_id == _RUN_ID
    assert config_version == config.config_version
    assert symbol == "SPY"
    assert order_action == "BUY"
    assert security_type == "EQ"
    assert quantity == 1
    assert preview_id == "pv1"
    assert estimated_cost == 100.0
    assert executed == 1
    assert refusal_gate is None
    assert etrade_order_id == "999"
    assert reasoning_summary  # non-empty placeholder (T4: never blank)
    assert signals_json == "[]"
    assert caps_snapshot_json  # non-empty JSON snapshot


def test_place_order_success_writes_reasoning_inherited_from_stored_preview(
    tmp_path: Path,
) -> None:
    """ADR-0004: place_order has no reasoning parameter of its own — it
    inherits the StoredPreview's reasoning_summary/signals_json, bound at
    preview_order time. This proves the real seam, not just the placeholder
    default (already covered by test_place_order_success_writes_t4_trade_log_receipt)."""
    preview, binding = _preview_and_binding("pv1")
    client = SpyClient(
        calls=[],
        place_result=OrderStatus(etrade_order_id="999", status="OPEN", filled_quantity=0),
    )
    gate = SpyGate()
    store = PreviewStore()
    real_signals_json = (
        '[{"source": "trader", "as_of": "2026-07-11T00:00:00+00:00", "summary": "x", "detail": {}}]'
    )
    store.put(
        StoredPreview(
            order=_order(),
            preview=preview,
            binding=binding,
            reasoning_summary="real decision reasoning",
            signals_json=real_signals_json,
        )
    )
    state = _state(tmp_path)

    tools.place_order(client, gate, store, state, _config(tmp_path), _RUN_ID, "pv1")

    row = state.conn.execute("SELECT reasoning_summary, signals_json FROM trade_log").fetchone()
    assert row is not None
    assert row[0] == "real decision reasoning"
    assert row[0] != tools._NO_PIPELINE_REASONING
    assert row[1] == real_signals_json


def test_place_order_refusal_writes_reasoning_inherited_from_stored_preview(
    tmp_path: Path,
) -> None:
    preview, binding = _preview_and_binding("pv1")
    client = SpyClient(calls=[])
    gate = SpyGate(place_refusal=Refusal(gate="kill-switch", reason="engaged", state={}))
    store = PreviewStore()
    store.put(
        StoredPreview(
            order=_order(),
            preview=preview,
            binding=binding,
            reasoning_summary="real decision reasoning",
            signals_json='[{"source": "trader"}]',
        )
    )
    state = _state(tmp_path)

    tools.place_order(client, gate, store, state, _config(tmp_path), _RUN_ID, "pv1")

    row = state.conn.execute("SELECT reasoning_summary, signals_json FROM trade_log").fetchone()
    assert row is not None
    assert row[0] == "real decision reasoning"
    assert row[1] == '[{"source": "trader"}]'


def test_place_order_success_logs_loudly_if_receipt_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Code-review finding: the order has ALREADY executed (irreversible,
    T2) by the time the T4 receipt is written. A state-write failure (e.g.
    concurrent-access "database is locked" — a real scenario given the local
    CLIs / remote listener / MCP server can all touch trading.db) must never
    be silently swallowed, and must never make the caller think the order
    itself failed (which could trigger an unsafe duplicate-place retry)."""
    preview, binding = _preview_and_binding("pv1")
    client = SpyClient(
        calls=[],
        place_result=OrderStatus(etrade_order_id="999", status="OPEN", filled_quantity=0),
    )
    gate = SpyGate()
    store = PreviewStore()
    store.put(StoredPreview(order=_order(), preview=preview, binding=binding))
    state = _state(tmp_path)
    config = _config(tmp_path)

    def _raise(*_a: object, **_k: object) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(state, "record_executed_trade", _raise)
    log_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        tools.logs, "log", lambda *a, **k: log_calls.append({"args": a, "kwargs": k}) or {}
    )

    result = tools.place_order(client, gate, store, state, config, _RUN_ID, "pv1")

    assert result["etrade_order_id"] == "999"  # the order DID execute
    error_calls = [c for c in log_calls if c["args"][1] == "error"]
    assert len(error_calls) == 1
    assert error_calls[0]["kwargs"]["etrade_order_id"] == "999"
    assert error_calls[0]["kwargs"]["symbol"] == "SPY"
    assert error_calls[0]["kwargs"]["run_id"] == _RUN_ID


def test_place_order_success_increments_daily_trade_count(tmp_path: Path) -> None:
    preview, binding = _preview_and_binding("pv1")
    client = SpyClient(
        calls=[],
        place_result=OrderStatus(etrade_order_id="999", status="OPEN", filled_quantity=0),
    )
    gate = SpyGate()
    store = PreviewStore()
    store.put(StoredPreview(order=_order(), preview=preview, binding=binding))
    state = _state(tmp_path)

    tools.place_order(client, gate, store, state, _config(tmp_path), _RUN_ID, "pv1")

    assert state.read_caps_state(today_utc()).trades_executed == 1


def test_place_order_second_call_with_same_preview_id_refuses(tmp_path: Path) -> None:
    preview, binding = _preview_and_binding("pv1")
    client = SpyClient(
        calls=[],
        place_result=OrderStatus(etrade_order_id="999", status="OPEN", filled_quantity=0),
    )
    gate = SpyGate()
    store = PreviewStore()
    store.put(StoredPreview(order=_order(), preview=preview, binding=binding))
    state = _state(tmp_path)
    config = _config(tmp_path)
    tools.place_order(client, gate, store, state, config, _RUN_ID, "pv1")  # first call consumes it

    result = tools.place_order(client, gate, store, state, config, _RUN_ID, "pv1")  # replay

    assert result["refused"] is True
    assert result["gate"] == "preview-required"


# --- register_tools: light FastMCP integration check ---


def test_register_tools_registers_all_six_tool_names(tmp_path: Path) -> None:
    from mcp.server.fastmcp import FastMCP

    app = FastMCP("test-etrade")
    client = SpyClient(calls=[])
    gate = SpyGate()
    store = PreviewStore()

    tools.register_tools(app, client, gate, store, _state(tmp_path), _config(tmp_path), _RUN_ID)  # type: ignore[arg-type]

    registered = {t.name for t in asyncio.run(app.list_tools())}
    assert registered == {
        "get_quote",
        "get_positions",
        "get_balances",
        "preview_order",
        "place_order",
        "get_order_status",
    }
