"""Caps wall — every SPEC §4.2 gate (invariant T1/T5).

Blocking in CI from day one, starting with `caps-required` (Phase 1/bootstrap);
Phase 2 grows this into the full try-to-violate-every-cap suite (ci.yml). Every
test here asserts the system REFUSES the violation with the exact SPEC §4.1
payload shape. Do not weaken (safety-wall skill) — a gate test failing means the
gate is wrong, not the test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from etrade_agent.config import AppConfig, ConfigError, load_config
from etrade_agent.etrade.models import (
    Balance,
    OrderAction,
    OrderPreview,
    OrderRequest,
    OrderType,
    Position,
    SecurityType,
)
from etrade_agent.server.app import create_app
from etrade_agent.server.safety import ConfiguredSafetyGate
from etrade_agent.store import db
from etrade_agent.store.state import StateStore, today_utc
from tests.conftest import VALID_CONFIG_TOML

REPO_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_NO_DEFAULT_LINES = (
    "pilot_amount_usd",
    "per_trade_pct",
    "daily_trade_limit",
    "daily_loss_pct",
)


@pytest.mark.parametrize("missing_field", REQUIRED_NO_DEFAULT_LINES)
def test_missing_cap_refuses_to_load(tmp_path: Path, missing_field: str) -> None:
    """Omitting any single cap (or the pilot amount) must raise ConfigError naming it."""
    lines = [line for line in VALID_CONFIG_TOML.splitlines() if not line.startswith(missing_field)]
    path = tmp_path / "config.toml"
    path.write_text("\n".join(lines))

    with pytest.raises(ConfigError, match=missing_field):
        load_config(path)


def test_example_config_as_shipped_refuses_to_load() -> None:
    """config.example.toml must NOT load as-is — proof there are no hidden cap defaults."""
    example = REPO_ROOT / "config" / "config.example.toml"
    assert example.exists(), "config/config.example.toml missing from repo"
    with pytest.raises(ConfigError):
        load_config(example)


def test_missing_config_file_refuses(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nonexistent.toml")


def test_server_factory_dies_without_caps(tmp_path: Path) -> None:
    """create_app must raise ConfigError (not NotImplementedError, not a default)
    when caps are absent — the startup gate runs before anything else exists."""
    path = tmp_path / "config.toml"
    path.write_text('config_version = 1\n[environment]\nmode = "sandbox"\n')

    with pytest.raises(ConfigError):
        create_app(path)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("per_trade_pct", "0"),
        ("per_trade_pct", "101"),
        ("per_trade_pct", "-5"),
        ("daily_trade_limit", "0"),
        ("daily_trade_limit", "-1"),
        ("daily_loss_pct", "0"),
        ("daily_loss_pct", "150"),
        ("pilot_amount_usd", "0"),
        ("pilot_amount_usd", "-100"),
    ],
)
def test_out_of_range_cap_refuses(tmp_path: Path, field: str, bad_value: str) -> None:
    """Invalid cap values are refused, never clamped or warned past (T5)."""
    lines = [
        f"{field} = {bad_value}" if line.startswith(field) else line
        for line in VALID_CONFIG_TOML.splitlines()
    ]
    path = tmp_path / "config.toml"
    path.write_text("\n".join(lines))

    with pytest.raises(ConfigError, match=field):
        load_config(path)


# ---------------------------------------------------------------------------
# §4.2 gate wall — ConfiguredSafetyGate, one test per gate's violation.
#
# VALID_CONFIG_TOML (tests/conftest.py): pilot_amount_usd=1000.0,
# per_trade_pct=10.0 (=> per-trade cap $100), daily_trade_limit=3,
# daily_loss_pct=3.0 (=> breaker trips at -$30), whitelist tier1=["SPY","AAPL"]
# enabled, policy long_only=true, allowed_security_types=["EQ"].
# ---------------------------------------------------------------------------


@dataclass
class FakeMarket:
    """Structurally satisfies ConfiguredSafetyGate's PositionsProvider dependency
    (get_positions/get_balances) without any live E*Trade call — the wall must
    stay hermetic."""

    positions: list[Position]
    balance: Balance

    def get_positions(self) -> list[Position]:
        return self.positions

    def get_balances(self) -> Balance:
        return self.balance


def _config(tmp_path: Path, toml_text: str = VALID_CONFIG_TOML) -> AppConfig:
    path = tmp_path / "config.toml"
    path.write_text(toml_text)
    return load_config(path)


def _gate(
    tmp_path: Path,
    *,
    toml_text: str = VALID_CONFIG_TOML,
    positions: list[Position] | None = None,
    balance: Balance | None = None,
) -> tuple[ConfiguredSafetyGate, StateStore]:
    config = _config(tmp_path, toml_text)
    conn = db.connect(tmp_path / "trading.db")
    state = StateStore(conn)
    market = FakeMarket(
        positions=positions or [],
        balance=balance
        or Balance(account_value=1000.0, cash_available=1000.0, buying_power=1000.0),
    )
    return ConfiguredSafetyGate(config, market, state), state


def _order(
    symbol: str = "SPY",
    order_action: OrderAction = OrderAction.BUY,
    quantity: int = 1,
    security_type: SecurityType = SecurityType.EQ,
) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        order_action=order_action,
        quantity=quantity,
        security_type=security_type,
        order_type=OrderType.MARKET,
    )


def _preview(estimated_cost: float, preview_id: str = "wall-preview") -> OrderPreview:
    return OrderPreview(preview_id=preview_id, estimated_cost=estimated_cost, warnings=[])


def _assert_refusal_shape(payload: dict, gate_id: str) -> None:
    """SPEC §4.1: {"refused": true, "gate", "reason", "state"} — a parsed
    contract, never a message. `state` must be populated (non-empty) so the
    refusal is auditable (T4-adjacent discipline), never an empty placeholder."""
    assert payload["refused"] is True
    assert payload["gate"] == gate_id
    assert isinstance(payload["reason"], str) and payload["reason"]
    assert isinstance(payload["state"], dict) and payload["state"]


def test_kill_switch_refuses_place(tmp_path: Path) -> None:
    """Fresh DB ships kill_switch.engaged=1 (SPEC §4.3) — an otherwise-valid
    order must refuse at place, checked first of all gates (ADR-0003 point 1)."""
    gate, _state = _gate(tmp_path)
    order = _order()
    payload = gate.check_place(_preview(50.0), order).to_payload()  # type: ignore[union-attr]

    _assert_refusal_shape(payload, "kill-switch")
    assert payload["state"]["engaged"] is True


def test_capital_ceiling_refuses_when_cost_plus_exposure_exceeds_pilot_capital(
    tmp_path: Path,
) -> None:
    """Existing exposure $950 + a within-per-trade-cap $60 order = $1010 >
    $1000 pilot capital. Isolated from per-trade-cap: $60 <= the $100 cap."""
    gate, state = _gate(
        tmp_path,
        positions=[Position(symbol="AAPL", quantity=10, cost_basis=950.0, market_value=950.0)],
    )
    state.set_kill_switch(engaged=False, changed_by="wall-test")
    order = _order(quantity=1)
    payload = gate.check_priced_preview(_preview(60.0), order).to_payload()  # type: ignore[union-attr]

    _assert_refusal_shape(payload, "capital-ceiling")


def test_per_trade_cap_refuses_when_order_exceeds_pct_of_pilot_capital(tmp_path: Path) -> None:
    """No existing exposure, so capital-ceiling ($150 <= $1000) passes; the
    order alone ($150) exceeds the $100 per-trade cap (10% of $1000)."""
    gate, state = _gate(tmp_path)
    state.set_kill_switch(engaged=False, changed_by="wall-test")
    order = _order(quantity=1)
    payload = gate.check_priced_preview(_preview(150.0), order).to_payload()  # type: ignore[union-attr]

    _assert_refusal_shape(payload, "per-trade-cap")


def test_daily_trade_limit_refuses_at_the_configured_count(tmp_path: Path) -> None:
    """daily_trade_limit=3: three trades already executed today refuses a
    fourth, with everything else about the order valid."""
    gate, state = _gate(tmp_path)
    state.set_kill_switch(engaged=False, changed_by="wall-test")
    for _ in range(3):
        state.increment_trades_executed(today_utc())

    order = _order(quantity=1)
    payload = gate.check_place(_preview(50.0), order).to_payload()  # type: ignore[union-attr]

    _assert_refusal_shape(payload, "daily-trade-limit")


def test_loss_breaker_refuses_when_already_tripped(tmp_path: Path) -> None:
    gate, state = _gate(tmp_path)
    state.set_kill_switch(engaged=False, changed_by="wall-test")
    state.trip_breaker(today_utc())

    order = _order(quantity=1)
    payload = gate.check_place(_preview(50.0), order).to_payload()  # type: ignore[union-attr]

    _assert_refusal_shape(payload, "loss-breaker")


def test_loss_breaker_trips_on_live_unrealized_loss(tmp_path: Path) -> None:
    """daily_loss_pct=3.0 => trips at -$30 (3% of $1000 pilot capital).
    Unrealized P&L is computed live from FakeMarket positions (ADR-0003 point
    3), never from positions_cache. realized_pnl defaults to 0 on a fresh
    caps_state row (ADR-0003 point 9)."""
    gate, state = _gate(
        tmp_path,
        positions=[Position(symbol="AAPL", quantity=10, cost_basis=1000.0, market_value=950.0)],
    )
    state.set_kill_switch(engaged=False, changed_by="wall-test")

    order = _order(quantity=1)
    payload = gate.check_place(_preview(50.0), order).to_payload()  # type: ignore[union-attr]

    _assert_refusal_shape(payload, "loss-breaker")
    assert state.read_caps_state(today_utc()).breaker_tripped is True


def test_whitelist_refuses_unlisted_symbol_at_preview(tmp_path: Path) -> None:
    gate, _state = _gate(tmp_path)
    order = _order(symbol="TSLA")
    payload = gate.check_preview(order).to_payload()  # type: ignore[union-attr]

    _assert_refusal_shape(payload, "whitelist")
    assert payload["state"]["symbol"] == "TSLA"


def test_policy_long_only_refuses_a_sell_exceeding_held_quantity(tmp_path: Path) -> None:
    """No AAPL held; a SELL would open a short (T6 / policy-long-only)."""
    gate, _state = _gate(tmp_path, positions=[])
    order = _order(symbol="AAPL", order_action=OrderAction.SELL, quantity=10)
    payload = gate.check_preview(order).to_payload()  # type: ignore[union-attr]

    _assert_refusal_shape(payload, "policy-long-only")


def test_policy_security_type_refuses_when_not_in_allowed_list(tmp_path: Path) -> None:
    """allowed_security_types=[] refuses even a whitelisted-symbol, policy-long-
    only-compliant BUY, isolating this gate from whitelist/policy-long-only."""
    toml_text = VALID_CONFIG_TOML.replace(
        'allowed_security_types = ["EQ"]', "allowed_security_types = []"
    )
    gate, _state = _gate(tmp_path, toml_text=toml_text)
    order = _order(symbol="SPY", order_action=OrderAction.BUY)
    payload = gate.check_preview(order).to_payload()  # type: ignore[union-attr]

    _assert_refusal_shape(payload, "policy-security-type")


def test_gate_order_kill_switch_precedes_sizing_gates(tmp_path: Path) -> None:
    """ADR-0003 point 1: halts before sizing. An order that violates BOTH
    kill-switch (engaged, default) and per-trade-cap ($500 > $100 cap) must
    report kill-switch, the more operationally urgent reason."""
    gate, _state = _gate(tmp_path)
    order = _order(quantity=1)
    payload = gate.check_place(_preview(500.0), order).to_payload()  # type: ignore[union-attr]

    assert payload["gate"] == "kill-switch"


def test_gate_order_whitelist_precedes_per_trade_cap(tmp_path: Path) -> None:
    """ADR-0003 point 1: legality before sizing. An order that violates BOTH
    whitelist (unlisted symbol) and per-trade-cap ($500 > $100 cap), with
    kill-switch disengaged and no other halt active, must report whitelist."""
    gate, state = _gate(tmp_path)
    state.set_kill_switch(engaged=False, changed_by="wall-test")
    order = _order(symbol="TSLA", quantity=1)
    payload = gate.check_place(_preview(500.0), order).to_payload()  # type: ignore[union-attr]

    assert payload["gate"] == "whitelist"


def test_gate_order_kill_switch_precedes_legality_gates(tmp_path: Path) -> None:
    """ADR-0003 point 1: halts before legality specifically (not just before
    sizing, covered above). An order that violates BOTH kill-switch (engaged,
    default) and whitelist (unlisted symbol) must report kill-switch."""
    gate, _state = _gate(tmp_path)
    order = _order(symbol="TSLA", quantity=1)
    payload = gate.check_place(_preview(50.0), order).to_payload()  # type: ignore[union-attr]

    assert payload["gate"] == "kill-switch"
