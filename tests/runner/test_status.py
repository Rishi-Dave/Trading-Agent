"""Tests for runner/status.py (SPEC §7 Phase 5, §9): per-run status reports —
run id, decisions, orders, refusals, duration, errors — written to
status/<run_id>.json, on every exit path a decision run can take
(ADR-0006 Step 0 #2), best-effort (an observability failure must never fail
the run/process it's reporting on)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from etrade_agent.runner.decision_run import OrderOutcome, RunSummary
from etrade_agent.runner.status import (
    build_status_report,
    write_status_report,
    write_status_report_best_effort,
)


def _summary() -> RunSummary:
    return RunSummary(
        run_id="run-1",
        decisions_considered=2,
        orders_skipped=0,
        outcomes=[
            OrderOutcome(
                symbol="SPY", action="BUY", executed=True, refusal_gate=None, etrade_order_id="o-1"
            ),
            OrderOutcome(symbol="AAPL", action="BUY", executed=False, refusal_gate="per-trade-cap"),
        ],
    )


# --- build_status_report -----------------------------------------------------


def test_build_status_report_from_a_completed_summary() -> None:
    report = build_status_report("run-1", _summary(), stage="completed", duration_seconds=1.5)

    assert report["run_id"] == "run-1"
    assert report["stage"] == "completed"
    assert report["duration_seconds"] == 1.5
    assert report["decisions_considered"] == 2
    assert report["orders_skipped"] == 0
    assert report["executed_count"] == 1
    assert report["refused_count"] == 1
    assert report["errors"] == []


def test_build_status_report_orders_carry_the_full_outcome_shape() -> None:
    report = build_status_report("run-1", _summary(), stage="completed", duration_seconds=1.5)

    assert report["orders"] == [
        {
            "symbol": "SPY",
            "action": "BUY",
            "executed": True,
            "refusal_gate": None,
            "etrade_order_id": "o-1",
        },
        {
            "symbol": "AAPL",
            "action": "BUY",
            "executed": False,
            "refusal_gate": "per-trade-cap",
            "etrade_order_id": None,
        },
    ]


def test_build_status_report_refusals_are_the_non_executed_orders_only() -> None:
    report = build_status_report("run-1", _summary(), stage="completed", duration_seconds=1.5)

    assert len(report["refusals"]) == 1
    assert report["refusals"][0]["symbol"] == "AAPL"
    assert report["refusals"][0]["refusal_gate"] == "per-trade-cap"


def test_build_status_report_with_no_summary_reports_zeroed_counts_and_errors() -> None:
    report = build_status_report(
        "run-2",
        None,
        stage="startup-error",
        duration_seconds=0.1,
        errors=[{"type": "ConfigError", "message": "missing caps"}],
    )

    assert report["decisions_considered"] == 0
    assert report["orders_skipped"] == 0
    assert report["executed_count"] == 0
    assert report["refused_count"] == 0
    assert report["orders"] == []
    assert report["refusals"] == []
    assert report["errors"] == [{"type": "ConfigError", "message": "missing caps"}]


def test_build_status_report_defaults_errors_to_empty_list() -> None:
    report = build_status_report("run-3", None, stage="skipped-kill-switch", duration_seconds=0.0)

    assert report["errors"] == []


# --- write_status_report ------------------------------------------------------


def test_write_status_report_writes_json_to_status_dir_run_id(tmp_path: Path) -> None:
    report = build_status_report("run-3", _summary(), stage="completed", duration_seconds=0.2)

    path = write_status_report(tmp_path / "status", "run-3", report)

    assert path == tmp_path / "status" / "run-3.json"
    assert json.loads(path.read_text()) == report


def test_write_status_report_creates_the_status_dir_if_missing(tmp_path: Path) -> None:
    report = build_status_report("run-4", None, stage="completed", duration_seconds=0.0)
    status_dir = tmp_path / "nested" / "status"

    path = write_status_report(status_dir, "run-4", report)

    assert path.exists()


def test_write_status_report_redacts_secret_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NTFY_TOPIC", "super-secret-topic")
    report = build_status_report(
        "run-5",
        None,
        stage="config-error",
        duration_seconds=0.0,
        errors=[{"type": "ConfigError", "message": "leaked super-secret-topic in message"}],
    )

    path = write_status_report(tmp_path / "status", "run-5", report)

    text = path.read_text()
    assert "super-secret-topic" not in text
    assert "[REDACTED]" in text


# --- write_status_report_best_effort -----------------------------------------


def test_write_status_report_best_effort_returns_the_written_path(tmp_path: Path) -> None:
    report = build_status_report("run-6", None, stage="completed", duration_seconds=0.0)

    result = write_status_report_best_effort(tmp_path / "status", "run-6", report)

    assert result == tmp_path / "status" / "run-6.json"


def test_write_status_report_best_effort_never_raises_on_a_bad_path(tmp_path: Path) -> None:
    # A file already exists where the status_dir needs to be a directory —
    # mkdir(parents=True) raises NotADirectoryError; best-effort must swallow it.
    blocker = tmp_path / "status"
    blocker.write_text("not a directory")
    report = build_status_report("run-7", None, stage="completed", duration_seconds=0.0)

    result = write_status_report_best_effort(blocker, "run-7", report)

    assert result is None
