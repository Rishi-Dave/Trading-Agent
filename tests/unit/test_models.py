"""OrderRequest field validation (SPEC §5.2).

Note: non-EQ security types and short-sell actions are *accepted by the model* on
purpose — the model stays wide so scaling to riskier instruments is a policy flip
(T6). REFUSING them is the safety gate's job, tested in the Phase 2 cap wall.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from etrade_agent.etrade.models import OrderAction, OrderRequest, OrderType, SecurityType


def _order(**overrides: object) -> OrderRequest:
    base: dict[str, object] = {
        "symbol": "SPY",
        "order_action": OrderAction.BUY,
        "quantity": 1,
        "order_type": OrderType.MARKET,
    }
    return OrderRequest.model_validate({**base, **overrides})


def test_minimal_buy_order_valid() -> None:
    order = _order()
    assert order.security_type is SecurityType.EQ
    assert order.limit_price is None


def test_extensibility_fields_exist_for_future_policy_change() -> None:
    """T6: the fields for riskier instruments exist NOW (policy gates refuse them later)."""
    order = _order()
    assert hasattr(order, "security_type")
    assert hasattr(order, "order_action")
    assert order.security_type.value == "EQ"


@pytest.mark.parametrize("quantity", [0, -1])
def test_nonpositive_quantity_rejected(quantity: int) -> None:
    with pytest.raises(ValidationError):
        _order(quantity=quantity)


def test_limit_order_requires_limit_price() -> None:
    with pytest.raises(ValidationError, match="limit_price"):
        _order(order_type=OrderType.LIMIT)
    order = _order(order_type=OrderType.LIMIT, limit_price=100.5)
    assert order.limit_price == 100.5


def test_market_order_rejects_limit_price() -> None:
    with pytest.raises(ValidationError, match="limit_price"):
        _order(order_type=OrderType.MARKET, limit_price=100.5)


def test_empty_symbol_rejected() -> None:
    with pytest.raises(ValidationError):
        _order(symbol="")
