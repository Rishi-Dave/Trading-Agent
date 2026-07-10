"""SQLite access (SPEC §5.1): WAL mode, forward-only migrations. Implemented in Phase 2."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the store (WAL mode) and apply pending migrations."""
    raise NotImplementedError("Phase 2 (SPEC §7)")


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply schema.MIGRATIONS newer than schema_migrations.version, in order."""
    raise NotImplementedError("Phase 2 (SPEC §7)")
