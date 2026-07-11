"""Tests for the decision-run orchestration loop (SPEC §7 Phase 4).

Two layers: the `_decision_to_order` mapping (unit) and `execute_decisions`'s
per-decision orchestration (integration-lite, with a fake gate/client so
orchestration logic is proven independent of gate correctness — the run wall,
tests/wall/phase4/, proves the same loop against the REAL ConfiguredSafetyGate).
`run_decision`'s full pipeline assembly gets a couple of focused tests here too;
the wall is still the authority for "executes <= caps" against real fixtures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from etrade_agent.config import AppConfig, load_config
from etrade_agent.etrade.client import PreviewBinding
from etrade_agent.etrade.models import (
    Balance,
    OrderPreview,
    OrderRequest,
    OrderStatus,
    Position,
    Quote,
)
from etrade_agent.pipeline.news import NewsItem
from etrade_agent.pipeline.steps import Action, Decision, Signal
from etrade_agent.runner.decision_run import (
    _decision_to_order,
    execute_decisions,
    run_decision,
)
from etrade_agent.server.app import Runtime
from etrade_agent.server.preview_store import PreviewStore
from etrade_agent.server.safety import Refusal
from etrade_agent.store import db
from etrade_agent.store.state import StateStore
from tests.conftest import VALID_CONFIG_TOML


def _config(tmp_path: Path) -> AppConfig:
    path = tmp_path / "config.toml"
    path.write_text(VALID_CONFIG_TOML)
    return load_config(path)


def _state(tmp_path: Path) -> StateStore:
    return StateStore(db.connect(tmp_path / "trading.db"))


def _signal(symbol: str) -> Signal:
    return Signal(
        source="test", as_of=datetime.now(UTC), summary="test signal", detail={"symbol": symbol}
    )


def _decision(action: Action, symbol: str = "SPY", quantity: int = 1) -> Decision:
    return Decision(
        action=action,
        symbol=symbol,
        quantity=quantity,
        confidence=0.8,
        reasoning_summary="test reasoning",
        signals=(_signal(symbol),),
    )


class _NotifyCollector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, title: str, message: str) -> None:
        self.calls.append((title, message))


# --- fake gates (orchestration-focused; the wall exercises the real gate) --


class _AllowGate:
    def check_preview(self, order: OrderRequest) -> Refusal | None:
        return None

    def check_priced_preview(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        return None

    def check_place(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        return None


class _RefuseAtPreviewGate:
    def check_preview(self, order: OrderRequest) -> Refusal | None:
        return Refusal(gate="whitelist", reason="simulated preview-time refusal", state={})

    def check_priced_preview(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        return None

    def check_place(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        return None


class _RefuseAtPlaceGate:
    def check_preview(self, order: OrderRequest) -> Refusal | None:
        return None

    def check_priced_preview(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        return None

    def check_place(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        return Refusal(gate="daily-trade-limit", reason="simulated place-time refusal", state={})


# --- fake EtradeClient: order methods only (execute_decisions tests) -------


@dataclass
class _FakeOrderClient:
    next_preview_id: str = "pv-1"
    next_order_id: str = "order-1"

    def preview_order(self, order: OrderRequest) -> tuple[OrderPreview, PreviewBinding]:
        preview = OrderPreview(preview_id=self.next_preview_id, estimated_cost=50.0, warnings=[])
        binding = PreviewBinding(
            preview_ids=[{"previewId": self.next_preview_id}],
            order_type="EQ",
            order_block=[{}],
            client_order_id="fake-client-order",
        )
        return preview, binding

    def place_from_binding(self, binding: PreviewBinding) -> OrderStatus:
        return OrderStatus(etrade_order_id=self.next_order_id, status="OPEN", filled_quantity=0)


# --- fake EtradeClient: order + market-data methods (run_decision tests) ---


@dataclass
class _FakeMarketOrderClient(_FakeOrderClient):
    quote_last: float = 450.0
    positions: list[Position] = field(default_factory=list)

    def get_quote(self, symbol: str) -> Quote:
        return Quote(
            symbol=symbol,
            bid=self.quote_last - 0.5,
            ask=self.quote_last + 0.5,
            last=self.quote_last,
            volume=1_000_000,
            as_of=datetime.now(UTC),
        )

    def get_positions(self) -> list[Position]:
        return self.positions

    def get_balances(self) -> Balance:
        return Balance(account_value=1000.0, cash_available=1000.0, buying_power=1000.0)


@dataclass
class _SimpleFakeLLMClient:
    def complete(self, prompt: str, *, allowed_tools: list[str] | None = None) -> str:
        if prompt.startswith("[trader]"):
            return json.dumps(
                {
                    "action": "BUY",
                    "quantity": 1,
                    "confidence": 0.9,
                    "reasoning": "unit test reasoning",
                }
            )
        return json.dumps({"summary": "unit test signal", "detail": {}})


@dataclass
class _SimpleFakeNewsSource:
    def headlines(self, symbol: str, since: datetime) -> list[NewsItem]:
        return [
            NewsItem(
                symbol=symbol,
                headline="test headline",
                summary="test summary",
                source="fake",
                published_at=datetime.now(UTC),
            )
        ]


def _runtime(tmp_path: Path, *, gate: object, client: object | None = None) -> Runtime:
    return Runtime(
        config=_config(tmp_path),
        client=client or _FakeOrderClient(),  # type: ignore[arg-type]
        gate=gate,  # type: ignore[arg-type]
        store=PreviewStore(),
        state=_state(tmp_path),
        run_id="test-run",
    )


# --- _decision_to_order ------------------------------------------------------


def test_hold_decision_maps_to_no_order() -> None:
    assert _decision_to_order(_decision(Action.HOLD, quantity=0)) is None


def test_zero_quantity_decision_maps_to_no_order() -> None:
    assert _decision_to_order(_decision(Action.BUY, quantity=0)) is None


def test_negative_quantity_decision_maps_to_no_order() -> None:
    assert _decision_to_order(_decision(Action.BUY, quantity=-5)) is None


def test_buy_decision_maps_to_market_eq_buy_order() -> None:
    order = _decision_to_order(_decision(Action.BUY, symbol="AAPL", quantity=3))
    assert order is not None
    assert order.symbol == "AAPL"
    assert order.order_action.value == "BUY"
    assert order.quantity == 3
    assert order.order_type.value == "MARKET"
    assert order.security_type.value == "EQ"
    assert order.limit_price is None


def test_sell_decision_maps_to_market_eq_sell_order() -> None:
    order = _decision_to_order(_decision(Action.SELL, symbol="AAPL", quantity=2))
    assert order is not None
    assert order.order_action.value == "SELL"


def test_malformed_symbol_maps_to_no_order() -> None:
    assert _decision_to_order(_decision(Action.BUY, symbol="", quantity=1)) is None


# --- execute_decisions -------------------------------------------------------


def test_execute_decisions_skips_hold_with_no_order_attempt(tmp_path: Path) -> None:
    rt = _runtime(tmp_path, gate=_AllowGate())
    notify = _NotifyCollector()

    summary = execute_decisions(rt, [_decision(Action.HOLD, quantity=0)], notify=notify)

    assert summary.decisions_considered == 1
    assert summary.orders_skipped == 1
    assert summary.outcomes == []
    row = rt.state.conn.execute("SELECT COUNT(*) FROM trade_log").fetchone()
    assert row[0] == 0
    assert notify.calls == []


def test_execute_decisions_executes_an_allowed_buy(tmp_path: Path) -> None:
    rt = _runtime(tmp_path, gate=_AllowGate())
    notify = _NotifyCollector()

    summary = execute_decisions(
        rt, [_decision(Action.BUY, symbol="SPY", quantity=1)], notify=notify
    )

    assert summary.decisions_considered == 1
    assert summary.orders_skipped == 0
    assert len(summary.outcomes) == 1
    outcome = summary.outcomes[0]
    assert outcome.symbol == "SPY"
    assert outcome.executed is True
    assert outcome.refusal_gate is None
    assert outcome.etrade_order_id == "order-1"

    row = rt.state.conn.execute(
        "SELECT executed, reasoning_summary, signals_json FROM trade_log"
    ).fetchone()
    assert row[0] == 1
    assert row[1] == "test reasoning"
    assert row[2] != "[]"
    assert any("Trade executed" in title for title, _ in notify.calls)


def test_execute_decisions_records_preview_time_refusal(tmp_path: Path) -> None:
    rt = _runtime(tmp_path, gate=_RefuseAtPreviewGate())
    notify = _NotifyCollector()

    summary = execute_decisions(
        rt, [_decision(Action.BUY, symbol="SPY", quantity=1)], notify=notify
    )

    assert len(summary.outcomes) == 1
    outcome = summary.outcomes[0]
    assert outcome.executed is False
    assert outcome.refusal_gate == "whitelist"

    row = rt.state.conn.execute("SELECT executed, refusal_gate FROM trade_log").fetchone()
    assert row[0] == 0
    assert row[1] == "whitelist"
    assert not any("Trade executed" in title for title, _ in notify.calls)


def test_execute_decisions_records_place_time_refusal(tmp_path: Path) -> None:
    rt = _runtime(tmp_path, gate=_RefuseAtPlaceGate())
    notify = _NotifyCollector()

    summary = execute_decisions(
        rt, [_decision(Action.BUY, symbol="SPY", quantity=1)], notify=notify
    )

    assert len(summary.outcomes) == 1
    outcome = summary.outcomes[0]
    assert outcome.executed is False
    assert outcome.refusal_gate == "daily-trade-limit"


def test_execute_decisions_handles_multiple_decisions_independently(tmp_path: Path) -> None:
    rt = _runtime(tmp_path, gate=_AllowGate())
    notify = _NotifyCollector()
    decisions = [
        _decision(Action.HOLD, symbol="AAPL", quantity=0),
        _decision(Action.BUY, symbol="SPY", quantity=1),
    ]

    summary = execute_decisions(rt, decisions, notify=notify)

    assert summary.decisions_considered == 2
    assert summary.orders_skipped == 1
    assert len(summary.outcomes) == 1
    assert summary.outcomes[0].symbol == "SPY"


# --- run_decision -------------------------------------------------------------


def test_run_decision_executes_full_loop_and_writes_receipts(tmp_path: Path) -> None:
    rt = _runtime(tmp_path, gate=_AllowGate(), client=_FakeMarketOrderClient())
    rt.state.set_kill_switch(engaged=False, changed_by="test-setup")
    notify = _NotifyCollector()

    summary = run_decision(
        rt, llm=_SimpleFakeLLMClient(), news=_SimpleFakeNewsSource(), notify=notify
    )

    assert summary is not None
    assert summary.decisions_considered >= 1
    assert len(summary.outcomes) >= 1
    assert summary.outcomes[0].executed is True

    row = rt.state.conn.execute("SELECT executed, reasoning_summary FROM trade_log").fetchone()
    assert row is not None
    assert row[0] == 1
    assert row[1] == "unit test reasoning"
    assert any("complete" in title.lower() for title, _ in notify.calls)


def test_run_decision_skips_entirely_when_kill_switch_engaged(tmp_path: Path) -> None:
    # A fresh DB ships kill_switch ENGAGED by default (SPEC §4.3) — no explicit
    # engage() call needed to exercise this path.
    rt = _runtime(tmp_path, gate=_AllowGate(), client=_FakeMarketOrderClient())
    notify = _NotifyCollector()

    summary = run_decision(
        rt, llm=_SimpleFakeLLMClient(), news=_SimpleFakeNewsSource(), notify=notify
    )

    assert summary is None
    row = rt.state.conn.execute("SELECT COUNT(*) FROM trade_log").fetchone()
    assert row[0] == 0


def test_run_decision_logs_advisory_notes_to_durable_jsonl(tmp_path: Path) -> None:
    rt = _runtime(tmp_path, gate=_AllowGate(), client=_FakeMarketOrderClient())
    rt.state.set_kill_switch(engaged=False, changed_by="test-setup")
    notify = _NotifyCollector()
    log_dir = tmp_path / "logs"

    run_decision(
        rt,
        llm=_SimpleFakeLLMClient(),
        news=_SimpleFakeNewsSource(),
        notify=notify,
        log_dir=log_dir,
    )

    files = list(log_dir.glob("*.jsonl"))
    assert len(files) == 1
    records = [json.loads(line) for line in files[0].read_text().splitlines()]
    notes_records = [r for r in records if r.get("data", {}).get("notes") is not None]
    assert len(notes_records) == 1
    notes = notes_records[0]["data"]["notes"]
    assert "risk_advisories" in notes
    assert "risk_advisory_llm" in notes
