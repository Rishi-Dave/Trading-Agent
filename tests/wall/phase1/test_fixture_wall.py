"""Phase 1 wall — fixture replay + schema-drift (SPEC §7 Phase 1 row).

Every fixture recorded from the real sandbox (fixtures/etrade/, etrade-fixtures
skill) must parse into its pydantic model. This is what catches upstream
E*Trade schema drift — assumptions baked into client.py's parse_* functions
that don't match reality. Concretely: this exact mechanism caught real bugs
live during Phase 1 development, before any of them could ship — no
"cashBuyingPower" field in balance responses, no "estimatedTotalAmount" field
in preview responses, IRA accounts reporting accountType="MARGIN" rather than
"INDIVIDUAL_RETIREMENT". Fixtures are ground truth; a fixture that stops
parsing means the client's assumptions are wrong, not the fixture
(safety-wall skill) — re-record and fix the parser, never edit the fixture to
match broken output.

`phase1` marker (conftest.py, this dir) isolates this from the day-one-blocking
caps wall — CI's `safety-wall` job (`-m "wall and not phase1"`) is unaffected.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from etrade_agent.etrade import client

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "etrade"

_ENDPOINTS = (
    "get_quote",
    "get_balances",
    "get_positions",
    "preview_order",
    "place_order",
    "get_order_status",
)


def _fixtures(prefix: str) -> list[Path]:
    return sorted(FIXTURES_DIR.glob(f"{prefix}.*.json"))


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def test_every_spec_endpoint_has_at_least_one_recorded_fixture() -> None:
    """A missing fixture is a silent coverage gap, not caught by the
    parametrized tests below (zero params -> zero test items -> silently
    green). This is the explicit tripwire."""
    missing = [e for e in _ENDPOINTS if not _fixtures(e)]
    assert not missing, f"no recorded fixture for: {missing}"


@pytest.mark.parametrize("path", _fixtures("get_quote"), ids=lambda p: p.name)
def test_get_quote_fixture_replays(path: Path) -> None:
    quote = client.parse_quote(_load(path))
    assert quote.symbol
    assert quote.bid >= 0
    assert quote.ask >= 0


@pytest.mark.parametrize("path", _fixtures("get_balances"), ids=lambda p: p.name)
def test_get_balances_fixture_replays(path: Path) -> None:
    balance = client.parse_balance(_load(path))
    assert balance.account_value is not None
    assert balance.cash_available is not None
    assert balance.buying_power is not None


@pytest.mark.parametrize("path", _fixtures("get_positions"), ids=lambda p: p.name)
def test_get_positions_fixture_replays(path: Path) -> None:
    positions = client.parse_positions(_load(path))
    assert isinstance(positions, list)
    for position in positions:
        assert position.symbol


@pytest.mark.parametrize("path", _fixtures("preview_order"), ids=lambda p: p.name)
def test_preview_order_fixture_replays(path: Path) -> None:
    preview, preview_ids_raw = client.parse_preview(_load(path), quantity=1, price_basis=100.0)
    assert preview.preview_id
    assert preview.estimated_cost >= 0
    assert preview_ids_raw


@pytest.mark.parametrize("path", _fixtures("place_order"), ids=lambda p: p.name)
def test_place_order_fixture_replays(path: Path) -> None:
    status = client.parse_place(_load(path))
    assert status.etrade_order_id
    assert status.status


@pytest.mark.parametrize("path", _fixtures("get_order_status"), ids=lambda p: p.name)
def test_get_order_status_fixture_replays(path: Path) -> None:
    # Filename convention: get_order_status.orderid-<id>.<date>.json
    order_id = path.name.split("orderid-", 1)[1].split(".", 1)[0]
    status = client.parse_order_status(_load(path), order_id)
    assert status.etrade_order_id == order_id
    assert status.status
