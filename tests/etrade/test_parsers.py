"""Pure parsing of E*Trade JSON payloads into pydantic models (SPEC §5.2).

No network: these exercise etrade_agent.etrade.client's module-level parse_*
functions directly against hand-built dicts shaped like documented E*Trade
responses. Schema-drift against the *real* sandbox is a separate wall test
(tests/wall/phase1/) once fixtures are recorded (etrade-fixtures skill).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from etrade_agent.etrade import client
from etrade_agent.etrade.models import OrderAction, OrderRequest, OrderType, SecurityType


def test_parse_quote() -> None:
    payload = {
        "QuoteResponse": {
            "QuoteData": [
                {
                    "dateTimeUTC": 1720612800,
                    "Product": {"symbol": "SPY", "securityType": "EQ"},
                    "All": {
                        "bid": 411.20,
                        "ask": 411.25,
                        "lastTrade": 411.23,
                        "totalVolume": 12345678,
                    },
                }
            ]
        }
    }

    quote = client.parse_quote(payload)

    assert quote.symbol == "SPY"
    assert quote.bid == 411.20
    assert quote.ask == 411.25
    assert quote.last == 411.23
    assert quote.volume == 12345678
    assert quote.as_of == datetime.fromtimestamp(1720612800, tz=UTC)


def test_parse_positions_multiple() -> None:
    payload = {
        "PortfolioResponse": {
            "AccountPortfolio": [
                {
                    "Position": [
                        {
                            "Product": {"symbol": "AAPL"},
                            "quantity": 10.0,
                            "pricePaid": 150.23,
                            "marketValue": 1600.10,
                        },
                        {
                            "Product": {"symbol": "MSFT"},
                            "quantity": 5.0,
                            "pricePaid": 300.00,
                            "marketValue": 1550.00,
                        },
                    ]
                }
            ]
        }
    }

    positions = client.parse_positions(payload)

    assert len(positions) == 2
    assert positions[0].symbol == "AAPL"
    assert positions[0].quantity == 10.0
    assert positions[0].cost_basis == 150.23
    assert positions[0].market_value == 1600.10
    assert positions[1].symbol == "MSFT"


def test_parse_positions_empty_portfolio() -> None:
    payload = {"PortfolioResponse": {"AccountPortfolio": [{}]}}

    assert client.parse_positions(payload) == []


def test_parse_balance() -> None:
    # Real sandbox shape (verified live 2026-07-10): no top-level "cashBuyingPower"
    # field exists. For a v1 cash account (T6, long-only) buying power IS cash
    # available for investment — there's no margin concept in scope.
    payload = {
        "BalanceResponse": {
            "accountType": "CASH",
            "Cash": {"fundsForOpenOrdersCash": 0.0, "moneyMktBalance": 0.0},
            "Computed": {
                "cashAvailableForInvestment": 4300.00,
                "cashAvailableForWithdrawal": 4300.00,
                "netCash": 4300.00,
                "RealTimeValues": {"totalAccountValue": 10500.55, "netMv": 6200.55},
            },
        }
    }

    balance = client.parse_balance(payload)

    assert balance.account_value == 10500.55
    assert balance.cash_available == 4300.00
    assert balance.buying_power == 4300.00


def test_parse_preview_extracts_id_cost_warnings_and_raw_preview_ids() -> None:
    # Real sandbox shape (verified live 2026-07-10): there is NO total-cost field
    # anywhere in the response — only estimatedCommission/estimatedFees. The
    # notional value must be computed client-side (quantity * price_basis) since
    # Phase 2's capital-ceiling/per-trade-cap gates depend on this being right
    # (ADR-0002). Sandbox is also canned/fixed data (etrade-fixtures skill) — the
    # response echoes a fake symbol/quantity, so quantity comes from the caller's
    # own OrderRequest, never parsed out of the response.
    payload = {
        "PreviewOrderResponse": {
            "PreviewIds": [{"previewId": 123456789}],
            "Order": [
                {
                    "estimatedCommission": 6.99,
                    "estimatedFees": 0.50,
                    "messages": {"Message": [{"description": "Estimated commission applies."}]},
                }
            ],
        }
    }

    preview, preview_ids_raw = client.parse_preview(payload, quantity=10, price_basis=100.0)

    assert preview.preview_id == "123456789"
    assert preview.estimated_cost == 10 * 100.0 + 6.99 + 0.50
    assert preview.warnings == ["Estimated commission applies."]
    assert preview_ids_raw == [{"previewId": 123456789}]


def test_parse_preview_no_messages_gives_empty_warnings() -> None:
    payload = {
        "PreviewOrderResponse": {
            "PreviewIds": [{"previewId": 1}],
            "Order": [{}],
        }
    }

    preview, _ = client.parse_preview(payload, quantity=1, price_basis=10.0)

    assert preview.warnings == []


def test_parse_preview_missing_commission_and_fees_defaults_to_zero() -> None:
    payload = {"PreviewOrderResponse": {"PreviewIds": [{"previewId": 1}], "Order": [{}]}}

    preview, _ = client.parse_preview(payload, quantity=2, price_basis=50.0)

    assert preview.estimated_cost == 100.0


def test_parse_place() -> None:
    payload = {"PlaceOrderResponse": {"OrderIds": [{"orderId": 987654321}]}}

    status = client.parse_place(payload)

    assert status.etrade_order_id == "987654321"
    assert status.status == "OPEN"
    assert status.filled_quantity == 0
    assert status.avg_price is None


def test_parse_order_status_finds_matching_order() -> None:
    payload = {
        "OrdersResponse": {
            "Order": [
                {
                    "orderId": 111,
                    "OrderDetail": [
                        {
                            "status": "OPEN",
                            "Instrument": [{"filledQuantity": 0}],
                        }
                    ],
                },
                {
                    "orderId": 987654321,
                    "OrderDetail": [
                        {
                            "status": "EXECUTED",
                            "Instrument": [{"filledQuantity": 10, "averageExecutionPrice": 411.25}],
                        }
                    ],
                },
            ]
        }
    }

    status = client.parse_order_status(payload, "987654321")

    assert status.etrade_order_id == "987654321"
    assert status.status == "EXECUTED"
    assert status.filled_quantity == 10
    assert status.avg_price == 411.25


def test_parse_order_status_missing_order_raises() -> None:
    payload = {"OrdersResponse": {"Order": []}}

    with pytest.raises(ValueError, match="404"):
        client.parse_order_status(payload, "404")


def test_build_order_payload_market_order() -> None:
    order = OrderRequest(
        symbol="SPY",
        order_action=OrderAction.BUY,
        quantity=5,
        security_type=SecurityType.EQ,
        order_type=OrderType.MARKET,
    )

    body = client.build_order_payload(order, "abc123")

    req = body["PreviewOrderRequest"]
    assert req["orderType"] == "EQ"
    assert req["clientOrderId"] == "abc123"
    order_block = req["Order"][0]
    assert order_block["priceType"] == "MARKET"
    assert order_block["limitPrice"] == ""
    instrument = order_block["Instrument"][0]
    assert instrument["Product"] == {"securityType": "EQ", "symbol": "SPY"}
    assert instrument["orderAction"] == "BUY"
    assert instrument["quantity"] == 5


def test_build_order_payload_limit_order_sets_limit_price() -> None:
    order = OrderRequest(
        symbol="AAPL",
        order_action=OrderAction.SELL,
        quantity=2,
        order_type=OrderType.LIMIT,
        limit_price=190.50,
    )

    body = client.build_order_payload(order, "xyz789")

    order_block = body["PreviewOrderRequest"]["Order"][0]
    assert order_block["priceType"] == "LIMIT"
    assert order_block["limitPrice"] == "190.5"
