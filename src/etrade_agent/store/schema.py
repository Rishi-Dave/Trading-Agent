"""SQLite DDL (SPEC §5.1). Migrations are forward-only numbered entries in MIGRATIONS."""

from __future__ import annotations

# Migration 1 — initial schema. The kill switch ships ENGAGED on a fresh DB (SPEC §4.3).
_MIGRATION_0001 = """
CREATE TABLE trade_log (
    id                  INTEGER PRIMARY KEY,
    ts_utc              TEXT NOT NULL,
    run_id              TEXT NOT NULL,
    config_version      INTEGER NOT NULL,
    symbol              TEXT NOT NULL,
    order_action        TEXT NOT NULL,
    security_type       TEXT NOT NULL,
    quantity            INTEGER NOT NULL,
    preview_id          TEXT,
    estimated_cost      REAL,
    executed            INTEGER NOT NULL DEFAULT 0,
    refusal_gate        TEXT,
    etrade_order_id     TEXT,
    reasoning_summary   TEXT NOT NULL,   -- T4
    signals_json        TEXT NOT NULL,   -- T4
    caps_snapshot_json  TEXT NOT NULL    -- T4
);

CREATE TABLE caps_state (
    date_utc            TEXT PRIMARY KEY,
    trades_executed     INTEGER NOT NULL DEFAULT 0,
    realized_pnl        REAL NOT NULL DEFAULT 0,
    breaker_tripped     INTEGER NOT NULL DEFAULT 0,
    breaker_tripped_ts  TEXT,
    breaker_reset_ts    TEXT,
    breaker_reset_by    TEXT
);

CREATE TABLE kill_switch (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    engaged     INTEGER NOT NULL DEFAULT 1,
    changed_ts  TEXT NOT NULL,
    changed_by  TEXT NOT NULL,
    note        TEXT
);

INSERT INTO kill_switch (id, engaged, changed_ts, changed_by, note)
VALUES (1, 1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), 'schema',
        'fresh database ships engaged (SPEC 4.3)');

CREATE TABLE positions_cache (
    symbol      TEXT PRIMARY KEY,
    quantity    REAL NOT NULL,
    cost_basis  REAL NOT NULL,
    as_of_ts    TEXT NOT NULL
);

CREATE TABLE schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_ts  TEXT NOT NULL
);
"""

MIGRATIONS: dict[int, str] = {1: _MIGRATION_0001}
