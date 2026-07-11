"""SQLite access (SPEC §5.1): WAL mode, forward-only migrations. Implemented in Phase 2."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from etrade_agent.store import schema


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the store (WAL mode) and apply pending migrations."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    apply_migrations(conn)
    return conn


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply schema.MIGRATIONS newer than schema_migrations.version, in order.

    Forward-only: never rewrites or re-applies a version already recorded in
    schema_migrations. On a fresh database that table doesn't exist yet (it's
    itself created by migration 1), so "no table" is treated as version 0.
    """
    current = _current_version(conn)
    for version in sorted(v for v in schema.MIGRATIONS if v > current):
        conn.executescript(schema.MIGRATIONS[version])
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_ts) "
            "VALUES (?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
            (version,),
        )
        conn.commit()


def _current_version(conn: sqlite3.Connection) -> int:
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if exists is None:
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    version = row[0] if row is not None else None
    return int(version) if version is not None else 0
