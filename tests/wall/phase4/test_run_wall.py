"""Phase 4 run wall (SPEC §7 Phase 4 row): "end-to-end sandbox run executes
<= caps and writes complete receipts."

Deterministic — a fake `LLMClient`/`NewsSource`/`EtradeClient` stand in for the
live WebSearch/claude-CLI-backed adapters and the live E*Trade sandbox, fed
from the same recorded responses under `fixtures/pipeline/` the Phase 3 wall
uses (ADR-0004 point 5) — no live network, no live model call, in this wall.

Unlike the Phase 3 wall, this wall drives the loop against a **REAL**
`ConfiguredSafetyGate` (not a fake `_AllowGate`), so "executes <= caps" is
proven against the actual §4.2 gate logic end-to-end, not asserted against a
stand-in (ADR-0005). Most scenarios here drive `execute_decisions` directly
with hand-built `Decision`s — this is the same real-gate proof the "full
pipeline" tests give, without needing a distinct LLM fixture per cap
scenario; one full-pipeline test (below) proves the whole
fetch->pipeline->execute->log->notify chain works together for real.

`phase4` marker (conftest.py, this dir) isolates this from the day-one-
blocking caps wall — CI's `safety-wall` job (`-m "wall and not phase1 and
not phase3 and not phase4"`) is unaffected.
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
from etrade_agent.runner.decision_run import execute_decisions, run_decision
from etrade_agent.server.app import Runtime
from etrade_agent.server.preview_store import _NO_PIPELINE_REASONING as NO_PIPELINE_REASONING
from etrade_agent.server.preview_store import PreviewStore
from etrade_agent.server.safety import ConfiguredSafetyGate
from etrade_agent.store import db
from etrade_agent.store.state import StateStore
from tests.conftest import VALID_CONFIG_TOML

# VALID_CONFIG_TOML (tests/conftest.py): pilot_amount_usd=1000, per_trade_pct=10
# (cap $100/trade), daily_trade_limit=3, whitelist tier1=["SPY","AAPL"].

_RUN_ID = "wall-phase4-run"

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "pipeline"


def _fixture_text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


def _fixture_json(name: str) -> object:
    return json.loads(_fixture_text(name))


_NEWS_RESPONSE = _fixture_text("news_analyst.symbol-SPY.2026-07-11.json")
_TECHNICAL_RESPONSE = _fixture_text("technical_analyst.symbol-SPY.2026-07-11.json")
_FUNDAMENTAL_RESPONSE = _fixture_text("fundamental_analyst.symbol-SPY.2026-07-11.json")
_TRADER_RESPONSE = _fixture_text("trader.symbol-SPY.2026-07-11.json")
_RISK_ADVISOR_RESPONSE = _fixture_text("risk_advisor.symbol-SPY.2026-07-11.json")


@dataclass
class FakeLLMClient:
    """Dispatches on the `[step-name]` tag every prompt template in
    pipeline/steps.py is prefixed with — no live model call. Mirrors
    tests/wall/phase3/test_pipeline_wall.py::FakeLLMClient exactly."""

    calls: list[str] = field(default_factory=list)

    def complete(self, prompt: str, *, allowed_tools: list[str] | None = None) -> str:
        self.calls.append(prompt)
        if prompt.startswith("[news-analyst]"):
            return _NEWS_RESPONSE
        if prompt.startswith("[technical-analyst]"):
            return _TECHNICAL_RESPONSE
        if prompt.startswith("[fundamental-analyst]"):
            return _FUNDAMENTAL_RESPONSE
        if prompt.startswith("[trader]"):
            return _TRADER_RESPONSE
        if prompt.startswith("[risk-advisor]"):
            return _RISK_ADVISOR_RESPONSE
        raise AssertionError(f"unexpected prompt with no fixture response: {prompt!r}")


@dataclass
class FakeNewsSource:
    items: list[NewsItem]

    def headlines(self, symbol: str, since: datetime) -> list[NewsItem]:
        return self.items


def _load_news_items(symbol: str) -> list[NewsItem]:
    raw = _fixture_json(f"news_headlines.symbol-{symbol}.2026-07-11.json")
    assert isinstance(raw, list)
    return [
        NewsItem(
            symbol=symbol,
            headline=entry["headline"],
            summary=entry["summary"],
            source="fixture-wire",
            published_at=datetime.fromisoformat(entry["published_at"]),
            url=entry.get("url"),
        )
        for entry in raw
    ]


@dataclass
class FakeOrderExecutionClient:
    """Stands in for EtradeClient (both the pipeline's MarketDataSource seam
    and the order-mutating preview/place methods `server/tools.py` needs) —
    canned market data, canned preview/place responses, no live network.
    `estimated_cost` is the one field each cap-boundary test controls
    directly, independent of `quote_last` (the gate checks the PREVIEW's
    estimated_cost, never a recomputed quote*quantity)."""

    quote_last: float = 450.0
    positions: list[Position] = field(default_factory=list)
    estimated_cost: float = 50.0  # well under VALID_CONFIG_TOML's $100 per-trade cap
    _next_id: int = 0

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

    def preview_order(self, order: OrderRequest) -> tuple[OrderPreview, PreviewBinding]:
        self._next_id += 1
        preview_id = f"pv-wall-{self._next_id}"
        preview = OrderPreview(
            preview_id=preview_id, estimated_cost=self.estimated_cost, warnings=[]
        )
        binding = PreviewBinding(
            preview_ids=[{"previewId": preview_id}],
            order_type="EQ",
            order_block=[{}],
            client_order_id=f"wall-client-order-{self._next_id}",
        )
        return preview, binding

    def place_from_binding(self, binding: PreviewBinding) -> OrderStatus:
        self._next_id += 1
        return OrderStatus(
            etrade_order_id=f"wall-order-{self._next_id}", status="OPEN", filled_quantity=0
        )


class _NotifyCollector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, title: str, message: str) -> None:
        self.calls.append((title, message))


def _config() -> AppConfig:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.toml"
        path.write_text(VALID_CONFIG_TOML)
        return load_config(path)


def _state(tmp_path: Path) -> StateStore:
    return StateStore(db.connect(tmp_path / "trading.db"))


def _runtime(
    tmp_path: Path,
    *,
    client: FakeOrderExecutionClient | None = None,
    notify: object | None = None,
) -> Runtime:
    config = _config()
    resolved_client = client or FakeOrderExecutionClient()
    state = _state(tmp_path)
    # The REAL gate (ADR-0005) — this is the whole point of this wall.
    # `notify` (ADR-0006, choice 3b) reaches the gate directly, same as
    # build_runtime wires it — a breaker trip notifies at the source, not
    # only when a caller happens to also pass notify into execute_decisions.
    gate = ConfiguredSafetyGate(config, resolved_client, state, notify=notify)  # type: ignore[arg-type]
    return Runtime(
        config=config,
        client=resolved_client,  # type: ignore[arg-type]
        gate=gate,
        store=PreviewStore(),
        state=state,
        run_id=_RUN_ID,
        notify=notify if notify is not None else (lambda title, message: None),  # type: ignore[arg-type]
    )


def _signal(symbol: str) -> Signal:
    return Signal(
        source="wall-fixture",
        as_of=datetime.now(UTC),
        summary="wall fixture signal",
        detail={"symbol": symbol},
    )


def _decision(action: Action, symbol: str = "SPY", quantity: int = 1) -> Decision:
    return Decision(
        action=action,
        symbol=symbol,
        quantity=quantity,
        confidence=0.8,
        reasoning_summary="wall fixture reasoning",
        signals=(_signal(symbol),),
    )


# --- 1. complete receipts (T4), full pipeline, real gate --------------------


def test_full_pipeline_run_writes_complete_receipts_against_real_gate(tmp_path: Path) -> None:
    notify = _NotifyCollector()
    rt = _runtime(tmp_path, notify=notify)
    rt.state.set_kill_switch(engaged=False, changed_by="wall-setup")
    status_dir = tmp_path / "status"

    summary = run_decision(
        rt,
        llm=FakeLLMClient(),
        news=FakeNewsSource(items=_load_news_items("SPY")),
        notify=notify,
        status_dir=status_dir,
    )

    assert summary is not None
    # VALID_CONFIG_TOML's whitelist enables both SPY and AAPL (tier1) — the
    # pipeline runs once per whitelisted symbol.
    assert summary.decisions_considered == 2
    assert summary.executed_count == 2
    assert summary.refused_count == 0

    rows = rt.state.conn.execute(
        "SELECT executed, reasoning_summary, signals_json, caps_snapshot_json FROM trade_log"
    ).fetchall()
    assert len(rows) == 2
    for executed, reasoning_summary, signals_json, caps_snapshot_json in rows:
        assert executed == 1
        assert reasoning_summary
        assert reasoning_summary != NO_PIPELINE_REASONING
        assert signals_json != "[]"
        assert json.loads(caps_snapshot_json)  # non-empty, parseable
    assert sum("Trade executed" in title for title, _ in notify.calls) == 2

    # Phase 5 (SPEC §9): the daily digest fires at the end of the happy path.
    assert any("digest" in title.lower() for title, _ in notify.calls)

    # Phase 5 (SPEC §9): a status/<run_id>.json is written with the full
    # receipt shape — run id, decisions, orders, refusals, duration, errors.
    status_files = list(status_dir.glob("*.json"))
    assert len(status_files) == 1
    report = json.loads(status_files[0].read_text())
    assert report["run_id"] == rt.run_id
    assert report["stage"] == "completed"
    assert report["decisions_considered"] == 2
    assert report["executed_count"] == 2
    assert report["refused_count"] == 0
    assert len(report["orders"]) == 2
    assert report["refusals"] == []
    assert report["errors"] == []
    assert report["duration_seconds"] >= 0


# --- 2. executes <= caps: daily-trade-limit, real gate -----------------------


def test_execute_decisions_stops_exactly_at_daily_trade_limit_against_real_gate(
    tmp_path: Path,
) -> None:
    rt = _runtime(tmp_path)
    # Unlike test 6 below (which deliberately relies on the fresh-DB-ships-
    # engaged default), this test isolates daily-trade-limit specifically —
    # kill-switch is checked FIRST among check_place's halts (SPEC §4.2), so
    # it must be disengaged here or every attempt would refuse at
    # "kill-switch" before ever reaching "daily-trade-limit".
    rt.state.set_kill_switch(engaged=False, changed_by="wall-setup")
    notify = _NotifyCollector()
    decisions = [_decision(Action.BUY, symbol="SPY", quantity=1) for _ in range(5)]

    summary = execute_decisions(rt, decisions, notify=notify)

    assert summary.executed_count == 3  # VALID_CONFIG_TOML's daily_trade_limit
    assert summary.refused_count == 2
    refused = [o for o in summary.outcomes if not o.executed]
    assert all(o.refusal_gate == "daily-trade-limit" for o in refused)

    executed_rows = rt.state.conn.execute(
        "SELECT COUNT(*) FROM trade_log WHERE executed = 1"
    ).fetchone()[0]
    refused_rows = rt.state.conn.execute(
        "SELECT COUNT(*) FROM trade_log WHERE executed = 0 AND refusal_gate = 'daily-trade-limit'"
    ).fetchone()[0]
    assert executed_rows == 3
    assert refused_rows == 2


# --- 3. oversized order refused by per-trade-cap, real gate ------------------


def test_oversized_order_refused_by_per_trade_cap_against_real_gate(tmp_path: Path) -> None:
    rt = _runtime(tmp_path, client=FakeOrderExecutionClient(estimated_cost=200.0))
    notify = _NotifyCollector()

    summary = execute_decisions(
        rt, [_decision(Action.BUY, symbol="SPY", quantity=1)], notify=notify
    )

    assert summary.executed_count == 0
    assert summary.outcomes[0].refusal_gate == "per-trade-cap"
    row = rt.state.conn.execute("SELECT executed, refusal_gate FROM trade_log").fetchone()
    assert row == (0, "per-trade-cap")


# --- 4. HOLD decisions never become an order attempt --------------------------


def test_hold_decision_never_becomes_an_order_attempt_against_real_gate(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    notify = _NotifyCollector()

    summary = execute_decisions(
        rt, [_decision(Action.HOLD, symbol="SPY", quantity=0)], notify=notify
    )

    assert summary.orders_skipped == 1
    assert summary.outcomes == []
    count = rt.state.conn.execute("SELECT COUNT(*) FROM trade_log").fetchone()[0]
    assert count == 0


# --- 5. non-whitelisted symbol: refused, not silently dropped ----------------


def test_non_whitelisted_symbol_refused_not_silently_dropped_against_real_gate(
    tmp_path: Path,
) -> None:
    rt = _runtime(tmp_path)
    notify = _NotifyCollector()

    summary = execute_decisions(
        rt, [_decision(Action.BUY, symbol="TSLA", quantity=1)], notify=notify
    )

    assert summary.executed_count == 0
    assert summary.outcomes[0].refusal_gate == "whitelist"
    row = rt.state.conn.execute("SELECT executed, refusal_gate, symbol FROM trade_log").fetchone()
    assert row == (0, "whitelist", "TSLA")


# --- 6. kill switch engaged: zero executed, real gate -------------------------


def test_kill_switch_engaged_refuses_at_place_against_real_gate(tmp_path: Path) -> None:
    # Fresh DB ships kill_switch ENGAGED by default (SPEC §4.3) — no explicit
    # engage() call needed. preview_order still succeeds (kill-switch isn't a
    # preview-time gate, §4.2) — place_order's check_place refuses it first
    # among the halts, before any other gate runs.
    rt = _runtime(tmp_path)
    notify = _NotifyCollector()

    summary = execute_decisions(
        rt, [_decision(Action.BUY, symbol="SPY", quantity=1)], notify=notify
    )

    assert summary.executed_count == 0
    assert summary.outcomes[0].refusal_gate == "kill-switch"
    assert not any("Trade executed" in title for title, _ in notify.calls)


# --- 7. advisory-risk notes persisted to durable JSONL (Phase 3 open thread) -


def test_advisory_notes_persisted_to_durable_jsonl_for_full_run(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    rt.state.set_kill_switch(engaged=False, changed_by="wall-setup")
    notify = _NotifyCollector()
    log_dir = tmp_path / "logs"

    run_decision(
        rt,
        llm=FakeLLMClient(),
        news=FakeNewsSource(items=_load_news_items("SPY")),
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


# --- 8. loss-breaker trip fires a distinct notification, real gate (Phase 5) -


def test_loss_breaker_trip_fires_a_distinct_notification_against_real_gate(tmp_path: Path) -> None:
    # daily_loss_pct=3.0 (VALID_CONFIG_TOML) => threshold = -$30 (3% of the
    # $1000 pilot capital). A $50 markdown on a held SPY position breaches it.
    client = FakeOrderExecutionClient(
        positions=[Position(symbol="SPY", quantity=1, cost_basis=450.0, market_value=400.0)]
    )
    notify = _NotifyCollector()
    rt = _runtime(tmp_path, client=client, notify=notify)
    rt.state.set_kill_switch(engaged=False, changed_by="wall-setup")

    summary = execute_decisions(
        rt,
        [
            _decision(Action.BUY, symbol="SPY", quantity=1),
            _decision(Action.BUY, symbol="AAPL", quantity=1),
        ],
        notify=notify,
    )

    assert summary.executed_count == 0
    assert all(o.refusal_gate == "loss-breaker" for o in summary.outcomes)
    breaker_notifications = [t for t, _ in notify.calls if "breaker" in t.lower()]
    # At most once per day (ADR-0006, choice 3b): the second refused order
    # hits the already-tripped branch, which never re-notifies.
    assert len(breaker_notifications) == 1
