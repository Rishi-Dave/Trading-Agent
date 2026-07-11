"""store/db.py: WAL-mode connect + forward-only migrations (SPEC §5.1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from etrade_agent.store import db


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def test_connect_creates_all_schema_tables(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "trading.db")
    try:
        tables = _table_names(conn)
        assert {
            "trade_log",
            "caps_state",
            "kill_switch",
            "positions_cache",
            "schema_migrations",
        } <= tables
    finally:
        conn.close()


def test_connect_enables_wal_mode(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "trading.db")
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_connect_ships_kill_switch_engaged(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "trading.db")
    try:
        row = conn.execute("SELECT engaged FROM kill_switch WHERE id = 1").fetchone()
        assert row is not None
        assert row[0] == 1
    finally:
        conn.close()


def test_connect_records_applied_migration_version(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "trading.db")
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        assert row[0] == 1
    finally:
        conn.close()


def test_apply_migrations_is_idempotent_on_reconnect(tmp_path: Path) -> None:
    """A second connect() against the same file must not re-run migration 1
    (which would fail on CREATE TABLE / re-insert the kill_switch seed row)."""
    path = tmp_path / "trading.db"
    conn1 = db.connect(path)
    conn1.close()

    conn2 = db.connect(path)
    try:
        count = conn2.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        assert count == 1
        kill_switch_rows = conn2.execute("SELECT COUNT(*) FROM kill_switch").fetchone()[0]
        assert kill_switch_rows == 1
    finally:
        conn2.close()


def test_connect_creates_parent_directories(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "dir" / "trading.db"
    conn = db.connect(nested)
    try:
        assert nested.exists()
    finally:
        conn.close()
