"""Typed E*Trade REST client (SPEC §5.2). Sandbox first; base URL from environment mode.

Parsing is split into pure module functions (`parse_*`, `build_order_payload`) so
replay/schema-drift tests can exercise them against a fixture dict with no network
(etrade-fixtures skill). `EtradeClient` itself is the transport: it does the HTTP
call and hands the JSON to the parser. The preview→place T2 binding is NOT held
here — `preview_order` returns a `PreviewBinding` the caller (server) stores; the
client's `place_from_binding` is pure transport, never a raw-order-to-place path.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from etrade_agent.etrade.models import (
    Balance,
    OrderPreview,
    OrderRequest,
    OrderStatus,
    Position,
    Quote,
)

SANDBOX_BASE_URL = "https://apisb.etrade.com"
# data/order path; never selected in Phase 1 (sandbox-prod skill)
PROD_BASE_URL = "https://api.etrade.com"


class HttpResponse(Protocol):
    def json(self) -> Any: ...
    def raise_for_status(self) -> None: ...


class HttpSession(Protocol):
    """Duck-typed subset of requests.Session / OAuth1Session used by EtradeClient.

    Lets tests inject a fake session (no network) without depending on the real
    requests_oauthlib types.
    """

    def get(self, url: str, params: dict[str, Any] | None = None) -> HttpResponse: ...
    def post(self, url: str, json: dict[str, Any] | None = None) -> HttpResponse: ...


@dataclass(frozen=True)
class PreviewBinding:
    """Everything `place_from_binding` needs to echo the exact previewed order (T2).

    E*Trade requires the place request to repeat the same `PreviewIds`, `Order`
    block, and `clientOrderId` used at preview time — this is that record. The
    server's PreviewStore holds one of these per issued preview_id.
    """

    preview_ids: list[dict[str, Any]]
    order_type: str
    order_block: list[dict[str, Any]]
    client_order_id: str


def parse_quote(payload: dict[str, Any]) -> Quote:
    data = payload["QuoteResponse"]["QuoteData"][0]
    product = data["Product"]
    all_ = data["All"]
    return Quote(
        symbol=product["symbol"],
        bid=float(all_["bid"]),
        ask=float(all_["ask"]),
        last=float(all_["lastTrade"]),
        volume=int(all_["totalVolume"]),
        as_of=datetime.fromtimestamp(data["dateTimeUTC"], tz=UTC),
    )


def parse_positions(payload: dict[str, Any]) -> list[Position]:
    account_portfolios = payload["PortfolioResponse"].get("AccountPortfolio", [])
    positions: list[Position] = []
    for account_portfolio in account_portfolios:
        for pos in account_portfolio.get("Position", []):
            positions.append(
                Position(
                    symbol=pos["Product"]["symbol"],
                    quantity=float(pos["quantity"]),
                    cost_basis=float(pos["pricePaid"]),
                    market_value=float(pos["marketValue"]),
                )
            )
    return positions


def parse_balance(payload: dict[str, Any]) -> Balance:
    """No top-level "cashBuyingPower" field exists in E*Trade's real response
    (verified live against sandbox). For a v1 cash account (T6, long-only) buying
    power IS cash available for investment — margin buying power is out of scope."""
    computed = payload["BalanceResponse"]["Computed"]
    cash_available = float(computed["cashAvailableForInvestment"])
    return Balance(
        account_value=float(computed["RealTimeValues"]["totalAccountValue"]),
        cash_available=cash_available,
        buying_power=cash_available,
    )


def _extract_messages(order_block: dict[str, Any]) -> list[str]:
    messages = order_block.get("messages", {}).get("Message", [])
    return [m["description"] for m in messages]


def parse_preview(
    payload: dict[str, Any], quantity: int, price_basis: float
) -> tuple[OrderPreview, list[dict[str, Any]]]:
    """Returns the frozen OrderPreview model plus the raw `PreviewIds` E*Trade
    issued (needed verbatim, as `PreviewBinding.preview_ids`, to place later).

    E*Trade's preview response carries NO total-cost field (verified live
    against sandbox) — only per-order `estimatedCommission`/`estimatedFees`.
    `estimated_cost` is computed here as notional (quantity * price_basis, from
    the caller's own OrderRequest — never the response's echoed quantity, which
    sandbox returns as canned/fake data) plus those real fee fields. This value
    is what Phase 2's capital-ceiling/per-trade-cap gates check (ADR-0002) —
    getting it right is a Phase 1 correctness requirement, not a refinement.
    """
    resp = payload["PreviewOrderResponse"]
    preview_ids_raw = resp["PreviewIds"]
    preview_id = str(preview_ids_raw[0]["previewId"])
    order_block = resp["Order"][0]
    commission = float(order_block.get("estimatedCommission", 0))
    fees = float(order_block.get("estimatedFees", 0))
    estimated_cost = quantity * price_basis + commission + fees
    preview = OrderPreview(
        preview_id=preview_id,
        estimated_cost=estimated_cost,
        warnings=_extract_messages(order_block),
    )
    return preview, preview_ids_raw


def parse_place(payload: dict[str, Any]) -> OrderStatus:
    order_id = str(payload["PlaceOrderResponse"]["OrderIds"][0]["orderId"])
    # Place responses don't report fills; get_order_status is the source of truth.
    return OrderStatus(etrade_order_id=order_id, status="OPEN", filled_quantity=0, avg_price=None)


def parse_order_status(payload: dict[str, Any], etrade_order_id: str) -> OrderStatus:
    for order in payload["OrdersResponse"].get("Order", []):
        if str(order.get("orderId")) == str(etrade_order_id):
            detail = order["OrderDetail"][0]
            instrument = detail["Instrument"][0]
            return OrderStatus(
                etrade_order_id=str(etrade_order_id),
                status=detail["status"],
                filled_quantity=int(instrument.get("filledQuantity", 0)),
                avg_price=instrument.get("averageExecutionPrice"),
            )
    raise ValueError(f"order {etrade_order_id} not found in orders response")


def build_order_payload(order: OrderRequest, client_order_id: str) -> dict[str, Any]:
    """Build the E*Trade PreviewOrderRequest body for `order`.

    The returned `Order` block is retained verbatim in `PreviewBinding.order_block`
    so `place_from_binding` echoes exactly what was previewed (T2).
    """
    order_block = {
        "allOrNone": False,
        "priceType": order.order_type.value,
        "orderTerm": "GOOD_FOR_DAY",
        "marketSession": "REGULAR",
        "stopPrice": "",
        "limitPrice": str(order.limit_price) if order.limit_price is not None else "",
        "Instrument": [
            {
                "Product": {"securityType": order.security_type.value, "symbol": order.symbol},
                "orderAction": order.order_action.value,
                "quantityType": "QUANTITY",
                "quantity": order.quantity,
            }
        ],
    }
    return {
        "PreviewOrderRequest": {
            "orderType": order.security_type.value,
            "clientOrderId": client_order_id,
            "Order": [order_block],
        }
    }


class EtradeClient:
    """One method per SPEC §5.2 endpoint. Implemented against sandbox in Phase 1."""

    def __init__(self, session: HttpSession, base_url: str, account_id_key: str) -> None:
        self._session = session
        self._base = base_url
        self._acct = account_id_key

    @property
    def account_id_key(self) -> str:
        """The accountIdKey actually in use — explicit or auto-resolved (T3:
        account-identifying; callers that need to scrub it, e.g.
        scripts/record_fixture.py, read it from here rather than re-deriving it)."""
        return self._acct

    @classmethod
    def connect(
        cls, session: HttpSession, base_url: str, account_id_key: str | None = None
    ) -> EtradeClient:
        """Resolve `accountIdKey` (via /v1/accounts/list) if not supplied.

        accountIdKey is account-identifying (T3) — never read from config.toml;
        callers pass it explicitly (e.g. `.env ETRADE_ACCOUNT_ID_KEY`) or leave it
        to auto-resolve to the single active brokerage sandbox account.
        """
        if account_id_key is None:
            response = session.get(f"{base_url}/v1/accounts/list")
            response.raise_for_status()
            accounts = response.json()["AccountListResponse"]["Accounts"]["Account"]
            account_id_key = _select_brokerage_account(accounts)
        return cls(session, base_url, account_id_key)

    def get_quote(self, symbol: str) -> Quote:
        response = self._session.get(f"{self._base}/v1/market/quote/{symbol}")
        response.raise_for_status()
        return parse_quote(response.json())

    def get_positions(self) -> list[Position]:
        response = self._session.get(f"{self._base}/v1/accounts/{self._acct}/portfolio")
        response.raise_for_status()
        return parse_positions(response.json())

    def get_balances(self) -> Balance:
        response = self._session.get(
            f"{self._base}/v1/accounts/{self._acct}/balance",
            params={"instType": "BROKERAGE", "realTimeNAV": "true"},
        )
        response.raise_for_status()
        return parse_balance(response.json())

    def preview_order(self, order: OrderRequest) -> tuple[OrderPreview, PreviewBinding]:
        """ADR-0002: E*Trade's preview response has no total-cost field, so
        estimated_cost is computed here from a price_basis — the order's own
        limit_price for LIMIT orders (its worst-case boundary, no extra call
        needed), or a fresh quote's last price for MARKET orders (fetched now,
        since fill price is unknown until the trade executes)."""
        price_basis = order.limit_price
        if price_basis is None:
            price_basis = self.get_quote(order.symbol).last
        client_order_id = secrets.token_hex(10)[:20]
        request_body = build_order_payload(order, client_order_id)
        response = self._session.post(
            f"{self._base}/v1/accounts/{self._acct}/orders/preview", json=request_body
        )
        response.raise_for_status()
        preview, preview_ids_raw = parse_preview(response.json(), order.quantity, price_basis)
        req = request_body["PreviewOrderRequest"]
        binding = PreviewBinding(
            preview_ids=preview_ids_raw,
            order_type=req["orderType"],
            order_block=req["Order"],
            client_order_id=client_order_id,
        )
        return preview, binding

    def place_from_binding(self, binding: PreviewBinding) -> OrderStatus:
        """The ONLY path to the E*Trade order endpoint (T2): echoes a stored
        PreviewBinding, never a raw order. There is no place-from-order method."""
        request_body = {
            "PlaceOrderRequest": {
                "orderType": binding.order_type,
                "clientOrderId": binding.client_order_id,
                "PreviewIds": binding.preview_ids,
                "Order": binding.order_block,
            }
        }
        response = self._session.post(
            f"{self._base}/v1/accounts/{self._acct}/orders/place", json=request_body
        )
        response.raise_for_status()
        return parse_place(response.json())

    def get_order_status(self, etrade_order_id: str) -> OrderStatus:
        response = self._session.get(f"{self._base}/v1/accounts/{self._acct}/orders")
        response.raise_for_status()
        return parse_order_status(response.json(), etrade_order_id)


def _select_brokerage_account(accounts: list[dict[str, Any]]) -> str:
    # accountMode=="IRA" is the real retirement signal (verified live against
    # sandbox) — accountType alone is NOT reliable; an IRA can report
    # accountType="MARGIN".
    active = [
        a for a in accounts if a.get("accountStatus") == "ACTIVE" and a.get("accountMode") != "IRA"
    ]
    if len(active) == 1:
        account_id_key = active[0]["accountIdKey"]
        return str(account_id_key)
    # T3: never include raw accountId/accountIdKey values in a refusal message —
    # this can end up in logs/launchd stderr. Counts only, no identifiers
    # (code-review finding; the plan explicitly required this to be redacted).
    raise ValueError(
        "cannot auto-resolve a single active brokerage account "
        f"(found {len(active)} eligible of {len(accounts)} total); "
        "set ETRADE_ACCOUNT_ID_KEY explicitly in .env"
    )
