"""Typed SQLite state access (SPEC §5.1, ADR-0003 point 8): kill switch,
caps_state, trade_log. The safety gates (server/safety.py), the manual
reset/kill CLIs (scripts/), the remote listener (scripts/remote_listener.py),
and the place_order receipt path (server/tools.py) all go through this layer
rather than issuing raw SQL against the connection directly — one place to
get the SPEC §5.1 column semantics right.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


def today_utc() -> str:
    """ADR-0003 point 2: 'today' for caps_state is the UTC calendar day (matches
    the schema's own strftime('...Z', 'now') timestamps). The pilot's once-daily
    market-open cadence (SPEC §9) never straddles the UTC midnight boundary, so
    this needs no timezone/DST handling. Shared by the safety gates, the
    reset/kill CLIs, and the remote listener — one definition of "today"."""
    return datetime.now(UTC).date().isoformat()


@dataclass(frozen=True)
class CapsStateSnapshot:
    """A read of one date's caps_state row (SPEC §5.1). A date with no row
    yet reads as this same shape, zeroed and un-tripped — never a silent
    fallback, just the correct starting state for a day nothing has happened
    on yet."""

    date_utc: str
    trades_executed: int
    realized_pnl: float
    breaker_tripped: bool
    breaker_tripped_ts: str | None
    breaker_reset_ts: str | None
    breaker_reset_by: str | None


class StateStore:
    """Wraps a `store.db.connect()`-opened SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- kill switch ---------------------------------------------------

    def is_kill_engaged(self) -> bool:
        row = self.conn.execute("SELECT engaged FROM kill_switch WHERE id = 1").fetchone()
        return bool(row[0]) if row is not None else True  # fail closed: no row -> engaged

    def set_kill_switch(self, *, engaged: bool, changed_by: str, note: str | None = None) -> None:
        self.conn.execute(
            "UPDATE kill_switch SET engaged = ?, "
            "changed_ts = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), "
            "changed_by = ?, note = ? WHERE id = 1",
            (1 if engaged else 0, changed_by, note),
        )
        self.conn.commit()

    # -- caps_state ------------------------------------------------------

    def read_caps_state(self, date_utc: str) -> CapsStateSnapshot:
        row = self.conn.execute(
            "SELECT date_utc, trades_executed, realized_pnl, breaker_tripped, "
            "breaker_tripped_ts, breaker_reset_ts, breaker_reset_by "
            "FROM caps_state WHERE date_utc = ?",
            (date_utc,),
        ).fetchone()
        if row is None:
            return CapsStateSnapshot(
                date_utc=date_utc,
                trades_executed=0,
                realized_pnl=0.0,
                breaker_tripped=False,
                breaker_tripped_ts=None,
                breaker_reset_ts=None,
                breaker_reset_by=None,
            )
        return CapsStateSnapshot(
            date_utc=row[0],
            trades_executed=row[1],
            realized_pnl=row[2],
            breaker_tripped=bool(row[3]),
            breaker_tripped_ts=row[4],
            breaker_reset_ts=row[5],
            breaker_reset_by=row[6],
        )

    def _ensure_caps_state_row(self, date_utc: str) -> None:
        self.conn.execute(
            "INSERT INTO caps_state (date_utc) VALUES (?) ON CONFLICT(date_utc) DO NOTHING",
            (date_utc,),
        )

    def increment_trades_executed(self, date_utc: str) -> None:
        self._bump_trades_executed(date_utc)
        self.conn.commit()

    def _bump_trades_executed(self, date_utc: str) -> None:
        self._ensure_caps_state_row(date_utc)
        self.conn.execute(
            "UPDATE caps_state SET trades_executed = trades_executed + 1 WHERE date_utc = ?",
            (date_utc,),
        )

    def trip_breaker(self, date_utc: str) -> None:
        self._ensure_caps_state_row(date_utc)
        self.conn.execute(
            "UPDATE caps_state SET breaker_tripped = 1, "
            "breaker_tripped_ts = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE date_utc = ?",
            (date_utc,),
        )
        self.conn.commit()

    def reset_breaker(self, date_utc: str, *, reset_by: str) -> None:
        self._ensure_caps_state_row(date_utc)
        self.conn.execute(
            "UPDATE caps_state SET breaker_tripped = 0, "
            "breaker_reset_ts = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), "
            "breaker_reset_by = ? WHERE date_utc = ?",
            (reset_by, date_utc),
        )
        self.conn.commit()

    # -- trade_log (T4 receipts) -----------------------------------------

    def write_trade_log(
        self,
        *,
        run_id: str,
        config_version: int,
        symbol: str,
        order_action: str,
        security_type: str,
        quantity: int,
        preview_id: str | None,
        estimated_cost: float | None,
        executed: bool,
        refusal_gate: str | None,
        etrade_order_id: str | None,
        reasoning_summary: str,
        signals_json: str,
        caps_snapshot_json: str,
    ) -> None:
        self._insert_trade_log_row(
            run_id=run_id,
            config_version=config_version,
            symbol=symbol,
            order_action=order_action,
            security_type=security_type,
            quantity=quantity,
            preview_id=preview_id,
            estimated_cost=estimated_cost,
            executed=executed,
            refusal_gate=refusal_gate,
            etrade_order_id=etrade_order_id,
            reasoning_summary=reasoning_summary,
            signals_json=signals_json,
            caps_snapshot_json=caps_snapshot_json,
        )
        self.conn.commit()

    def record_executed_trade(
        self,
        *,
        date_utc: str,
        run_id: str,
        config_version: int,
        symbol: str,
        order_action: str,
        security_type: str,
        quantity: int,
        preview_id: str | None,
        estimated_cost: float | None,
        executed: bool,
        refusal_gate: str | None,
        etrade_order_id: str | None,
        reasoning_summary: str,
        signals_json: str,
        caps_snapshot_json: str,
    ) -> None:
        """T4 receipt + daily-trade-count increment as ONE commit (code-review
        finding): place_order calls this once, after an irreversible E*Trade
        placement, so a crash between "row written" and "count bumped" can't
        leave one without the other."""
        self._insert_trade_log_row(
            run_id=run_id,
            config_version=config_version,
            symbol=symbol,
            order_action=order_action,
            security_type=security_type,
            quantity=quantity,
            preview_id=preview_id,
            estimated_cost=estimated_cost,
            executed=executed,
            refusal_gate=refusal_gate,
            etrade_order_id=etrade_order_id,
            reasoning_summary=reasoning_summary,
            signals_json=signals_json,
            caps_snapshot_json=caps_snapshot_json,
        )
        self._bump_trades_executed(date_utc)
        self.conn.commit()

    def _insert_trade_log_row(
        self,
        *,
        run_id: str,
        config_version: int,
        symbol: str,
        order_action: str,
        security_type: str,
        quantity: int,
        preview_id: str | None,
        estimated_cost: float | None,
        executed: bool,
        refusal_gate: str | None,
        etrade_order_id: str | None,
        reasoning_summary: str,
        signals_json: str,
        caps_snapshot_json: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO trade_log (ts_utc, run_id, config_version, symbol, order_action, "
            "security_type, quantity, preview_id, estimated_cost, executed, refusal_gate, "
            "etrade_order_id, reasoning_summary, signals_json, caps_snapshot_json) "
            "VALUES (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), "
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                config_version,
                symbol,
                order_action,
                security_type,
                quantity,
                preview_id,
                estimated_cost,
                1 if executed else 0,
                refusal_gate,
                etrade_order_id,
                reasoning_summary,
                signals_json,
                caps_snapshot_json,
            ),
        )
