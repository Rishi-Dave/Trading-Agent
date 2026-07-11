"""E*Trade API data models (SPEC §5.2).

OrderRequest deliberately carries `security_type` and `order_action` enums wider
than v1 policy allows (T6): riskier instruments become a policy-config change plus
ADR, not a model restructure. The *refusal* of non-EQ / short orders is the safety
gate's job (SPEC §4.2 `policy-*` gates), never this module's.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class OrderAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    # Future, policy-gated (T6): SELL_SHORT, BUY_TO_COVER, option actions.


class SecurityType(StrEnum):
    EQ = "EQ"
    # Future, policy-gated (T6): OPTN.


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class Quote(BaseModel):
    symbol: str
    bid: float
    ask: float
    last: float
    volume: int
    as_of: datetime


class Position(BaseModel):
    symbol: str
    quantity: float
    cost_basis: float
    market_value: float


def unrealized_pnl(positions: list[Position]) -> float:
    """Live unrealized P&L across positions (market_value - cost_basis,
    summed). The single shared definition of this calculation: both the
    loss-breaker gate (server/safety.py) and the daily digest
    (runner/decision_run.py) read from here, never two independently
    maintained copies that could silently drift apart (ADR-0006)."""
    return sum(p.market_value - p.cost_basis for p in positions)


class Balance(BaseModel):
    account_value: float
    cash_available: float
    buying_power: float


class OrderRequest(BaseModel):
    symbol: str = Field(min_length=1)
    order_action: OrderAction
    quantity: int = Field(gt=0)
    security_type: SecurityType = SecurityType.EQ
    order_type: OrderType
    limit_price: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def limit_price_iff_limit(self) -> OrderRequest:
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit_price is required for LIMIT orders")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("limit_price must be unset for MARKET orders")
        return self


class OrderPreview(BaseModel):
    preview_id: str
    estimated_cost: float
    warnings: list[str] = Field(default_factory=list)


class OrderStatus(BaseModel):
    etrade_order_id: str
    status: str
    filled_quantity: int
    avg_price: float | None = None
