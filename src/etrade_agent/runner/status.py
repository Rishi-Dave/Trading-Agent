"""Per-run status reports (SPEC §9): run id, decisions, orders, refusals, duration, errors."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def write_status_report(status_dir: Path, run_id: str, report: dict[str, Any]) -> Path:
    """Write status/<run_id>.json for the daily digest and monitoring (Phase 5)."""
    raise NotImplementedError("Phase 5 (SPEC §7)")
