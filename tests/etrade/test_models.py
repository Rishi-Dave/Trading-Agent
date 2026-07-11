"""Tests for etrade/models.py's pure helpers.

`unrealized_pnl` is the single shared definition of live unrealized P&L —
both server/safety.py's loss-breaker gate and runner/decision_run.py's daily
digest read from this one calculation (ADR-0006), never two independently
maintained copies that could silently drift apart.
"""

from __future__ import annotations

from etrade_agent.etrade.models import Position, unrealized_pnl


def test_unrealized_pnl_of_no_positions_is_zero() -> None:
    assert unrealized_pnl([]) == 0.0


def test_unrealized_pnl_sums_market_value_minus_cost_basis_across_positions() -> None:
    positions = [
        Position(symbol="SPY", quantity=1, cost_basis=450.0, market_value=400.0),
        Position(symbol="AAPL", quantity=2, cost_basis=200.0, market_value=230.0),
    ]

    # SPY: 400 - 450 = -50; AAPL: 230 - 200 = +30; total = -20
    assert unrealized_pnl(positions) == -20.0


def test_unrealized_pnl_of_a_single_profitable_position_is_positive() -> None:
    positions = [Position(symbol="AAPL", quantity=1, cost_basis=100.0, market_value=150.0)]

    assert unrealized_pnl(positions) == 50.0
