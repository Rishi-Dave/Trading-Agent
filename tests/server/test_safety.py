"""ConfiguredSafetyGate (SPEC §4.2, Phase 2). Gate *violations* are the cap
wall's job (tests/wall/test_caps_wall.py, one test per gate, never weakened).
This file covers what the wall doesn't: the "allow" path for a fully
compliant order, and fail-closed behavior on an unexpected exception
(server/CLAUDE.md: "on any uncertainty, exception, or missing state, refuse
the order")."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from etrade_agent.config import AppConfig, load_config
from etrade_agent.etrade.models import (
    Balance,
    OrderAction,
    OrderPreview,
    OrderRequest,
    OrderType,
    Position,
    SecurityType,
)
from etrade_agent.server.safety import ConfiguredSafetyGate
from etrade_agent.store import db
from etrade_agent.store.state import StateStore
from tests.conftest import VALID_CONFIG_TOML

# VALID_CONFIG_TOML: pilot_amount_usd=1000.0, per_trade_pct=10.0 (=> $100 cap),
# daily_trade_limit=3, daily_loss_pct=3.0, whitelist tier1=["SPY","AAPL"].


@dataclass
class FakeMarket:
    positions: list[Position]
    balance: Balance

    def get_positions(self) -> list[Position]:
        return self.positions

    def get_balances(self) -> Balance:
        return self.balance


@dataclass
class RaisingMarket:
    """A market provider that always fails — proves the gate fails closed."""

    def get_positions(self) -> list[Position]:
        raise RuntimeError("simulated E*Trade outage")

    def get_balances(self) -> Balance:
        raise RuntimeError("simulated E*Trade outage")


def _config(tmp_path: Path) -> AppConfig:
    path = tmp_path / "config.toml"
    path.write_text(VALID_CONFIG_TOML)
    return load_config(path)


def _state(tmp_path: Path) -> StateStore:
    conn = db.connect(tmp_path / "trading.db")
    return StateStore(conn)


def _compliant_order() -> OrderRequest:
    return OrderRequest(
        symbol="SPY",
        order_action=OrderAction.BUY,
        quantity=1,
        security_type=SecurityType.EQ,
        order_type=OrderType.MARKET,
    )


def _compliant_preview() -> OrderPreview:
    return OrderPreview(preview_id="p1", estimated_cost=50.0, warnings=[])


def _balance() -> Balance:
    return Balance(account_value=1000.0, cash_available=1000.0, buying_power=1000.0)


def test_check_preview_allows_a_fully_compliant_order(tmp_path: Path) -> None:
    gate = ConfiguredSafetyGate(
        _config(tmp_path),
        FakeMarket(positions=[], balance=_balance()),
        _state(tmp_path),
    )
    assert gate.check_preview(_compliant_order()) is None


def test_check_priced_preview_allows_within_all_caps(tmp_path: Path) -> None:
    gate = ConfiguredSafetyGate(
        _config(tmp_path),
        FakeMarket(positions=[], balance=_balance()),
        _state(tmp_path),
    )
    assert gate.check_priced_preview(_compliant_preview(), _compliant_order()) is None


def test_check_place_allows_a_fully_compliant_order(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.set_kill_switch(engaged=False, changed_by="test-setup")
    gate = ConfiguredSafetyGate(
        _config(tmp_path),
        FakeMarket(positions=[], balance=_balance()),
        state,
    )
    assert gate.check_place(_compliant_preview(), _compliant_order()) is None


def test_check_place_allows_a_sell_within_held_quantity(tmp_path: Path) -> None:
    """policy-long-only: SELL up to (not exceeding) currently-held quantity is
    allowed — only a short is refused."""
    state = _state(tmp_path)
    state.set_kill_switch(engaged=False, changed_by="test-setup")
    gate = ConfiguredSafetyGate(
        _config(tmp_path),
        FakeMarket(
            positions=[Position(symbol="AAPL", quantity=10, cost_basis=900.0, market_value=900.0)],
            balance=_balance(),
        ),
        state,
    )
    order = OrderRequest(
        symbol="AAPL",
        order_action=OrderAction.SELL,
        quantity=10,
        order_type=OrderType.MARKET,
    )
    preview = OrderPreview(preview_id="p1", estimated_cost=90.0, warnings=[])
    assert gate.check_place(preview, order) is None


def test_check_place_allows_a_large_sell_that_would_exceed_sizing_caps_if_miscounted(
    tmp_path: Path,
) -> None:
    """capital-ceiling/per-trade-cap must not double-count a SELL's own
    position as new exposure (code-review finding): a full-exit SELL of an
    appreciated position must never be blocked by sizing gates —
    policy-long-only's held-quantity check is the real bound for sells.
    Position market_value=950 (> the $100 per-trade cap and would push
    "exposure" past the $1000 ceiling if double-counted)."""
    state = _state(tmp_path)
    state.set_kill_switch(engaged=False, changed_by="test-setup")
    gate = ConfiguredSafetyGate(
        _config(tmp_path),
        FakeMarket(
            positions=[Position(symbol="AAPL", quantity=10, cost_basis=900.0, market_value=950.0)],
            balance=_balance(),
        ),
        state,
    )
    order = OrderRequest(
        symbol="AAPL", order_action=OrderAction.SELL, quantity=10, order_type=OrderType.MARKET
    )
    # estimated_cost mirrors the position's own market value, as ADR-0002 pt5's
    # client-side costing would compute for a full-exit MARKET sell.
    preview = OrderPreview(preview_id="p1", estimated_cost=950.0, warnings=[])

    assert gate.check_place(preview, order) is None


def test_check_priced_preview_allows_a_large_sell(tmp_path: Path) -> None:
    """Same double-counting bug, at the other call site for these two gates
    (server/tools.py::preview_order calls check_priced_preview right after
    pricing)."""
    gate = ConfiguredSafetyGate(
        _config(tmp_path),
        FakeMarket(
            positions=[Position(symbol="AAPL", quantity=10, cost_basis=900.0, market_value=950.0)],
            balance=_balance(),
        ),
        _state(tmp_path),
    )
    order = OrderRequest(
        symbol="AAPL", order_action=OrderAction.SELL, quantity=10, order_type=OrderType.MARKET
    )
    preview = OrderPreview(preview_id="p1", estimated_cost=950.0, warnings=[])

    assert gate.check_priced_preview(preview, order) is None


def test_check_priced_preview_allows_order_exactly_at_capital_ceiling(tmp_path: Path) -> None:
    """SPEC §4.2: capital-ceiling's pass condition is "<= pilot capital" —
    exactly at the boundary must allow, not refuse. per_trade_pct raised to
    100% here to isolate this from the (separately boundary-tested)
    per-trade-cap gate, which would otherwise also fire on a $1000 order."""
    toml_text = VALID_CONFIG_TOML.replace("per_trade_pct = 10.0", "per_trade_pct = 100.0")
    path = tmp_path / "config.toml"
    path.write_text(toml_text)
    config = load_config(path)
    gate = ConfiguredSafetyGate(
        config, FakeMarket(positions=[], balance=_balance()), _state(tmp_path)
    )
    preview = OrderPreview(preview_id="p1", estimated_cost=1000.0, warnings=[])  # == $1000 exactly

    assert gate.check_priced_preview(preview, _compliant_order()) is None


def test_check_priced_preview_allows_order_exactly_at_per_trade_cap(tmp_path: Path) -> None:
    """per-trade-cap's pass condition is "<= per_trade_pct% of pilot capital"
    — exactly at the $100 boundary (10% of $1000) must allow."""
    gate = ConfiguredSafetyGate(
        _config(tmp_path), FakeMarket(positions=[], balance=_balance()), _state(tmp_path)
    )
    preview = OrderPreview(preview_id="p1", estimated_cost=100.0, warnings=[])  # == $100 exactly

    assert gate.check_priced_preview(preview, _compliant_order()) is None


def test_check_place_trips_breaker_when_pnl_exactly_at_threshold(tmp_path: Path) -> None:
    """SPEC §4.2: loss-breaker trips at "<=" the threshold — unlike the
    sizing gates above, exactly AT this boundary is a REFUSAL (a trip
    condition, not a pass condition). daily_loss_pct=3.0 => threshold = -$30
    (3% of $1000 pilot capital); an unrealized loss of exactly $30 must trip."""
    state = _state(tmp_path)
    state.set_kill_switch(engaged=False, changed_by="test-setup")
    gate = ConfiguredSafetyGate(
        _config(tmp_path),
        FakeMarket(
            positions=[Position(symbol="AAPL", quantity=10, cost_basis=1000.0, market_value=970.0)],
            balance=_balance(),
        ),
        state,
    )

    refusal = gate.check_place(_compliant_preview(), _compliant_order())

    assert refusal is not None
    assert refusal.gate == "loss-breaker"


def test_check_place_fails_closed_on_unexpected_exception(tmp_path: Path) -> None:
    """server/CLAUDE.md: on any exception, refuse — never let it propagate and
    surface as a raw, message-corrupting error through FastMCP (ADR-0002 pt7)."""
    state = _state(tmp_path)
    state.set_kill_switch(engaged=False, changed_by="test-setup")
    gate = ConfiguredSafetyGate(_config(tmp_path), RaisingMarket(), state)

    refusal = gate.check_place(_compliant_preview(), _compliant_order())

    assert refusal is not None
    payload = refusal.to_payload()
    assert payload["refused"] is True
    assert isinstance(payload["gate"], str) and payload["gate"]
    assert isinstance(payload["reason"], str) and payload["reason"]


def test_check_preview_fails_closed_on_unexpected_exception(tmp_path: Path) -> None:
    gate = ConfiguredSafetyGate(_config(tmp_path), RaisingMarket(), _state(tmp_path))

    # policy-long-only needs positions; force it via a SELL so RaisingMarket is hit.
    order = OrderRequest(
        symbol="AAPL", order_action=OrderAction.SELL, quantity=1, order_type=OrderType.MARKET
    )
    refusal = gate.check_preview(order)

    assert refusal is not None
    assert refusal.to_payload()["refused"] is True
