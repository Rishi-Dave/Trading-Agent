"""Per-run status reports (SPEC §9, Phase 5): run id, decisions, orders,
refusals, duration, errors — written to status/<run_id>.json.

Written on every exit path a decision run can take (ADR-0006 Step 0 #2):
completed, kill-switch-skipped, and every runner/__main__.py startup/
unexpected-exception failure — not completed runs only. `build_status_report`
takes `summary: RunSummary | None` for exactly this reason: the four failure
paths and never reach a RunSummary at all.

`write_status_report_best_effort` never raises (mirrors notify/ntfy.py's
`build_notify` resilience contract): an observability failure must never
abort the run or process it's reporting on.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from etrade_agent import logs

if TYPE_CHECKING:
    from etrade_agent.runner.decision_run import RunSummary

_AGENT_ID = "etrade-runner"


def build_status_report(
    run_id: str,
    summary: RunSummary | None,
    *,
    stage: str,
    duration_seconds: float,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Assemble the SPEC §9 status report shape. `summary` is None on every
    path that never reached a RunSummary (kill-switch skip carries one path
    that does reach a summary-less report too, when the skip itself couldn't
    even reach the pipeline — see decision_run.py::run_decision); those
    reports carry zeroed counts and an empty orders/refusals list rather than
    a fabricated non-zero figure."""
    orders = [
        {
            "symbol": outcome.symbol,
            "action": outcome.action,
            "executed": outcome.executed,
            "refusal_gate": outcome.refusal_gate,
            "etrade_order_id": outcome.etrade_order_id,
        }
        for outcome in (summary.outcomes if summary is not None else [])
    ]
    refusals = [order for order in orders if not order["executed"]]
    return {
        "run_id": run_id,
        "stage": stage,
        "ts_utc": datetime.now(UTC).isoformat(),
        "duration_seconds": duration_seconds,
        "decisions_considered": summary.decisions_considered if summary is not None else 0,
        "orders_skipped": summary.orders_skipped if summary is not None else 0,
        "executed_count": summary.executed_count if summary is not None else 0,
        "refused_count": summary.refused_count if summary is not None else 0,
        "orders": orders,
        "refusals": refusals,
        "errors": errors or [],
    }


def write_status_report(status_dir: Path, run_id: str, report: dict[str, Any]) -> Path:
    """Write status/<run_id>.json. T3: the serialized report is routed
    through logs.redact before writing, in case an error message or exception
    string incidentally captured a secret env-var value (e.g. NTFY_TOPIC)."""
    status_dir.mkdir(parents=True, exist_ok=True)
    path = status_dir / f"{run_id}.json"
    text = logs.redact(json.dumps(report, indent=2, default=str))
    path.write_text(text)
    return path


def write_status_report_best_effort(
    status_dir: Path, run_id: str, report: dict[str, Any]
) -> Path | None:
    """Never raises — an observability failure (e.g. an unwritable status_dir)
    must not abort the run/process this report describes. Returns None (and
    logs a warning) instead of the written Path on failure."""
    try:
        return write_status_report(status_dir, run_id, report)
    except Exception as exc:
        logs.log(
            _AGENT_ID, "warning", "failed to write status report", error=str(exc), run_id=run_id
        )
        return None
