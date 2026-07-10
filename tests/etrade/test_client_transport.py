"""EtradeClient transport (SPEC §5.2): URL/param construction and T2 place echo.

A fake session (duck-typed .get/.post) stands in for OAuth1Session — no network,
no fixtures needed here (that's the replay wall). This proves the client calls
the right endpoints with the right shape, and that place_from_binding echoes
exactly the PreviewIds/Order/clientOrderId a preview issued (T2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from etrade_agent.etrade.client import EtradeClient, PreviewBinding
from etrade_agent.etrade.models import OrderAction, OrderRequest, OrderType


@dataclass
class FakeResponse:
    payload: Any

    def json(self) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        return None


@dataclass
class FakeSession:
    """Records every call; returns a queued response per call in order."""

    responses: list[FakeResponse]
    calls: list[tuple[str, str, dict[str, Any] | None]] = field(default_factory=list)

    def get(self, url: str, params: dict[str, Any] | None = None) -> FakeResponse:
        self.calls.append(("GET", url, params))
        return self.responses.pop(0)

    def post(self, url: str, json: dict[str, Any] | None = None) -> FakeResponse:
        self.calls.append(("POST", url, json))
        return self.responses.pop(0)


BASE = "https://apisb.etrade.com"
ACCOUNTS_LIST_PAYLOAD = {
    "AccountListResponse": {
        "Accounts": {
            "Account": [
                {
                    "accountId": "12345678",
                    "accountIdKey": "dGVzdA",
                    "accountType": "INDIVIDUAL",
                    "accountStatus": "ACTIVE",
                }
            ]
        }
    }
}


def test_connect_resolves_single_active_brokerage_account() -> None:
    session = FakeSession(responses=[FakeResponse(ACCOUNTS_LIST_PAYLOAD)])

    client = EtradeClient.connect(session, BASE)

    assert session.calls == [("GET", f"{BASE}/v1/accounts/list", None)]
    assert client.account_id_key == "dGVzdA"


def test_connect_uses_explicit_account_id_key_without_calling_accounts_list() -> None:
    session = FakeSession(responses=[])

    client = EtradeClient.connect(session, BASE, account_id_key="explicit-key")

    assert session.calls == []
    assert client.account_id_key == "explicit-key"


def test_connect_excludes_ira_accounts_even_when_account_type_says_margin() -> None:
    # Verified live against sandbox: an IRA account can report accountType=MARGIN
    # (not "INDIVIDUAL_RETIREMENT") — accountMode=="IRA" is the real retirement
    # signal, not accountType. A naive accountType-only filter would wrongly
    # include this account as eligible.
    ira_account = {
        "accountId": "1",
        "accountIdKey": "ira-key",
        "accountType": "MARGIN",
        "accountMode": "IRA",
        "accountStatus": "ACTIVE",
    }
    brokerage_account = {
        "accountId": "2",
        "accountIdKey": "brokerage-key",
        "accountType": "INDIVIDUAL",
        "accountMode": "CASH",
        "accountStatus": "ACTIVE",
    }
    payload = {"AccountListResponse": {"Accounts": {"Account": [ira_account, brokerage_account]}}}
    session = FakeSession(responses=[FakeResponse(payload)])

    client = EtradeClient.connect(session, BASE)

    assert client.account_id_key == "brokerage-key"


def test_connect_refuses_ambiguous_accounts() -> None:
    account_a = {
        "accountId": "87654321",
        "accountIdKey": "a",
        "accountType": "INDIVIDUAL",
        "accountStatus": "ACTIVE",
    }
    account_b = {
        "accountId": "87654322",
        "accountIdKey": "b",
        "accountType": "INDIVIDUAL",
        "accountStatus": "ACTIVE",
    }
    payload = {"AccountListResponse": {"Accounts": {"Account": [account_a, account_b]}}}
    session = FakeSession(responses=[FakeResponse(payload)])

    with pytest.raises(ValueError, match="cannot auto-resolve") as exc_info:
        EtradeClient.connect(session, BASE)

    # T3: the refusal message must never leak raw account-identifying values
    # (code-review finding — the plan explicitly required a redacted list).
    message = str(exc_info.value)
    assert "87654321" not in message
    assert "87654322" not in message


def test_get_quote_calls_market_quote_path() -> None:
    payload = {
        "QuoteResponse": {
            "QuoteData": [
                {
                    "dateTimeUTC": 1720612800,
                    "Product": {"symbol": "SPY"},
                    "All": {"bid": 1, "ask": 2, "lastTrade": 1.5, "totalVolume": 100},
                }
            ]
        }
    }
    session = FakeSession(responses=[FakeResponse(payload)])
    client = EtradeClient(session, BASE, "acctkey")

    quote = client.get_quote("SPY")

    assert session.calls == [("GET", f"{BASE}/v1/market/quote/SPY", None)]
    assert quote.symbol == "SPY"


def test_get_balances_sends_realtime_nav_params() -> None:
    payload = {
        "BalanceResponse": {
            "Computed": {
                "RealTimeValues": {"totalAccountValue": 1},
                "cashAvailableForInvestment": 1,
                "cashBuyingPower": 1,
            }
        }
    }
    session = FakeSession(responses=[FakeResponse(payload)])
    client = EtradeClient(session, BASE, "acctkey")

    client.get_balances()

    expected_params = {"instType": "BROKERAGE", "realTimeNAV": "true"}
    assert session.calls == [("GET", f"{BASE}/v1/accounts/acctkey/balance", expected_params)]


def test_get_positions_calls_portfolio_path() -> None:
    session = FakeSession(responses=[FakeResponse({"PortfolioResponse": {"AccountPortfolio": []}})])
    client = EtradeClient(session, BASE, "acctkey")

    client.get_positions()

    assert session.calls == [("GET", f"{BASE}/v1/accounts/acctkey/portfolio", None)]


def test_preview_order_limit_uses_limit_price_as_basis_with_no_extra_call() -> None:
    # E*Trade's preview response has no total-cost field (verified live) — for a
    # LIMIT order, limit_price is the caller's own worst-case boundary, so no
    # extra get_quote call is needed to compute estimated_cost (ADR-0002).
    preview_payload = {
        "PreviewOrderResponse": {
            "PreviewIds": [{"previewId": 555}],
            "Order": [{"estimatedCommission": 5.0, "estimatedFees": 0.0}],
        }
    }
    session = FakeSession(responses=[FakeResponse(preview_payload)])
    client = EtradeClient(session, BASE, "acctkey")
    order = OrderRequest(
        symbol="SPY",
        order_action=OrderAction.BUY,
        quantity=2,
        order_type=OrderType.LIMIT,
        limit_price=100.0,
    )

    preview, binding = client.preview_order(order)

    assert preview.preview_id == "555"
    assert preview.estimated_cost == 2 * 100.0 + 5.0
    assert isinstance(binding, PreviewBinding)
    assert binding.preview_ids == [{"previewId": 555}]
    assert len(session.calls) == 1  # no get_quote call for LIMIT orders
    method, url, body = session.calls[0]
    assert method == "POST"
    assert url == f"{BASE}/v1/accounts/acctkey/orders/preview"
    assert body is not None
    assert body["PreviewOrderRequest"]["clientOrderId"] == binding.client_order_id


def test_preview_order_market_fetches_quote_first_for_price_basis() -> None:
    quote_payload = {
        "QuoteResponse": {
            "QuoteData": [
                {
                    "dateTimeUTC": 1720612800,
                    "Product": {"symbol": "SPY"},
                    "All": {"bid": 99.0, "ask": 101.0, "lastTrade": 100.0, "totalVolume": 1},
                }
            ]
        }
    }
    preview_payload = {
        "PreviewOrderResponse": {
            "PreviewIds": [{"previewId": 555}],
            "Order": [{"estimatedCommission": 5.0, "estimatedFees": 0.0}],
        }
    }
    session = FakeSession(responses=[FakeResponse(quote_payload), FakeResponse(preview_payload)])
    client = EtradeClient(session, BASE, "acctkey")
    order = OrderRequest(
        symbol="SPY", order_action=OrderAction.BUY, quantity=3, order_type=OrderType.MARKET
    )

    preview, _ = client.preview_order(order)

    assert preview.estimated_cost == 3 * 100.0 + 5.0  # priced off quote.last
    assert session.calls[0] == ("GET", f"{BASE}/v1/market/quote/SPY", None)
    assert session.calls[1][0] == "POST"


def test_place_from_binding_echoes_preview_ids_order_and_client_order_id() -> None:
    place_payload = {"PlaceOrderResponse": {"OrderIds": [{"orderId": 999}]}}
    session = FakeSession(responses=[FakeResponse(place_payload)])
    client = EtradeClient(session, BASE, "acctkey")
    binding = PreviewBinding(
        preview_ids=[{"previewId": 555}],
        order_type="EQ",
        order_block=[{"priceType": "MARKET"}],
        client_order_id="clientorder1",
    )

    status = client.place_from_binding(binding)

    assert status.etrade_order_id == "999"
    method, url, body = session.calls[0]
    assert method == "POST"
    assert url == f"{BASE}/v1/accounts/acctkey/orders/place"
    assert body == {
        "PlaceOrderRequest": {
            "orderType": "EQ",
            "clientOrderId": "clientorder1",
            "PreviewIds": [{"previewId": 555}],
            "Order": [{"priceType": "MARKET"}],
        }
    }


def test_get_order_status_calls_orders_path() -> None:
    payload = {
        "OrdersResponse": {
            "Order": [
                {
                    "orderId": 42,
                    "OrderDetail": [{"status": "OPEN", "Instrument": [{"filledQuantity": 0}]}],
                }
            ]
        }
    }
    session = FakeSession(responses=[FakeResponse(payload)])
    client = EtradeClient(session, BASE, "acctkey")

    status = client.get_order_status("42")

    assert session.calls == [("GET", f"{BASE}/v1/accounts/acctkey/orders", None)]
    assert status.status == "OPEN"
