"""store/state.py: typed SQLite access for kill switch, caps_state, trade_log
(SPEC §5.1, ADR-0003 point 8). Gates, CLIs, and the place_order receipt path
all go through this layer rather than raw SQL."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from etrade_agent.store import db
from etrade_agent.store.state import StateStore, today_utc


@pytest.fixture
def state(tmp_path: Path) -> StateStore:
    conn = db.connect(tmp_path / "trading.db")
    return StateStore(conn)


def test_today_utc_is_a_utc_calendar_date_string() -> None:
    """ADR-0003 point 2: 'today' for caps_state is the UTC calendar day, matching
    the schema's own strftime('...Z', 'now') UTC timestamps — never local time
    or a market-session (ET) day."""
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", today_utc())
    assert today_utc() == datetime.now(UTC).date().isoformat()


def test_fresh_db_has_kill_switch_engaged(state: StateStore) -> None:
    assert state.is_kill_engaged() is True


def test_set_kill_switch_disengage(state: StateStore) -> None:
    state.set_kill_switch(engaged=False, changed_by="rishi", note="testing")
    assert state.is_kill_engaged() is False


def test_set_kill_switch_records_operator_and_note(state: StateStore) -> None:
    state.set_kill_switch(engaged=False, changed_by="rishi", note="disengage for testing")
    row = state.conn.execute("SELECT changed_by, note FROM kill_switch WHERE id = 1").fetchone()
    assert row[0] == "rishi"
    assert row[1] == "disengage for testing"


def test_set_kill_switch_engage_after_disengage(state: StateStore) -> None:
    state.set_kill_switch(engaged=False, changed_by="rishi", note="off")
    state.set_kill_switch(engaged=True, changed_by="rishi", note="back on")
    assert state.is_kill_engaged() is True


def test_read_caps_state_defaults_for_new_day(state: StateStore) -> None:
    """A date with no caps_state row yet reads as a zeroed, un-tripped default,
    never a fallback that masks a missing row (T5-adjacent: fail closed, don't
    silently invent numbers)."""
    snapshot = state.read_caps_state("2026-07-10")
    assert snapshot.trades_executed == 0
    assert snapshot.realized_pnl == 0.0
    assert snapshot.breaker_tripped is False


def test_increment_trades_executed_creates_row_and_increments(state: StateStore) -> None:
    state.increment_trades_executed("2026-07-10")
    assert state.read_caps_state("2026-07-10").trades_executed == 1
    state.increment_trades_executed("2026-07-10")
    assert state.read_caps_state("2026-07-10").trades_executed == 2


def test_increment_trades_executed_is_per_day(state: StateStore) -> None:
    state.increment_trades_executed("2026-07-10")
    state.increment_trades_executed("2026-07-11")
    assert state.read_caps_state("2026-07-10").trades_executed == 1
    assert state.read_caps_state("2026-07-11").trades_executed == 1


def test_trip_breaker_sets_tripped_and_timestamp(state: StateStore) -> None:
    state.trip_breaker("2026-07-10")
    snapshot = state.read_caps_state("2026-07-10")
    assert snapshot.breaker_tripped is True
    assert snapshot.breaker_tripped_ts is not None


def test_reset_breaker_clears_tripped_and_records_operator(state: StateStore) -> None:
    state.trip_breaker("2026-07-10")
    state.reset_breaker("2026-07-10", reset_by="rishi")
    snapshot = state.read_caps_state("2026-07-10")
    assert snapshot.breaker_tripped is False
    assert snapshot.breaker_reset_by == "rishi"
    assert snapshot.breaker_reset_ts is not None


def test_write_trade_log_persists_t4_receipt_columns(state: StateStore) -> None:
    state.write_trade_log(
        run_id="run-1",
        config_version=1,
        symbol="SPY",
        order_action="BUY",
        security_type="EQ",
        quantity=1,
        preview_id="preview-123",
        estimated_cost=500.0,
        executed=True,
        refusal_gate=None,
        etrade_order_id="order-456",
        reasoning_summary="placeholder — no pipeline yet (Phase 3)",
        signals_json="[]",
        caps_snapshot_json='{"trades_executed": 0}',
    )
    row = state.conn.execute(
        "SELECT reasoning_summary, signals_json, caps_snapshot_json, executed, "
        "etrade_order_id FROM trade_log"
    ).fetchone()
    assert row[0] == "placeholder — no pipeline yet (Phase 3)"
    assert row[1] == "[]"
    assert row[2] == '{"trades_executed": 0}'
    assert row[3] == 1
    assert row[4] == "order-456"


def test_write_trade_log_for_a_refusal_has_no_etrade_order_id(state: StateStore) -> None:
    state.write_trade_log(
        run_id="run-1",
        config_version=1,
        symbol="TSLA",
        order_action="BUY",
        security_type="EQ",
        quantity=1000,
        preview_id=None,
        estimated_cost=None,
        executed=False,
        refusal_gate="per-trade-cap",
        etrade_order_id=None,
        reasoning_summary="refused before pipeline reasoning was recorded",
        signals_json="[]",
        caps_snapshot_json="{}",
    )
    row = state.conn.execute(
        "SELECT executed, refusal_gate, etrade_order_id FROM trade_log"
    ).fetchone()
    assert row[0] == 0
    assert row[1] == "per-trade-cap"
    assert row[2] is None


def test_record_executed_trade_writes_receipt_and_increments_count(state: StateStore) -> None:
    """Code-review finding: place_order calls write_trade_log then
    increment_trades_executed as two separate operations after an
    irreversible E*Trade call, with no guarantee both land together.
    record_executed_trade does both in one call, one commit."""
    state.record_executed_trade(
        date_utc="2026-07-10",
        run_id="run-1",
        config_version=1,
        symbol="SPY",
        order_action="BUY",
        security_type="EQ",
        quantity=1,
        preview_id="preview-123",
        estimated_cost=500.0,
        executed=True,
        refusal_gate=None,
        etrade_order_id="order-456",
        reasoning_summary="placeholder",
        signals_json="[]",
        caps_snapshot_json="{}",
    )

    row = state.conn.execute("SELECT etrade_order_id FROM trade_log").fetchone()
    assert row[0] == "order-456"
    assert state.read_caps_state("2026-07-10").trades_executed == 1


def test_record_executed_trade_creates_caps_state_row_if_absent(state: StateStore) -> None:
    """Same guarantee increment_trades_executed already has: no pre-existing
    caps_state row for the day isn't a crash, it's day-1's zero-to-one."""
    state.record_executed_trade(
        date_utc="2026-07-10",
        run_id="run-1",
        config_version=1,
        symbol="SPY",
        order_action="BUY",
        security_type="EQ",
        quantity=1,
        preview_id="preview-123",
        estimated_cost=500.0,
        executed=True,
        refusal_gate=None,
        etrade_order_id="order-456",
        reasoning_summary="placeholder",
        signals_json="[]",
        caps_snapshot_json="{}",
    )

    assert state.read_caps_state("2026-07-10").trades_executed == 1
