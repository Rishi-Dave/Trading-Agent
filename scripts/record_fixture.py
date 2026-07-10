"""Record a scrubbed E*Trade sandbox fixture (SPEC §5.4, etrade-fixtures skill).

Usage:
  uv run python scripts/record_fixture.py get_quote SYMBOL
  uv run python scripts/record_fixture.py get_balances
  uv run python scripts/record_fixture.py get_positions
  uv run python scripts/record_fixture.py preview_order SYMBOL ACTION QTY ORDER_TYPE [LIMIT_PRICE]
  uv run python scripts/record_fixture.py place_order SYMBOL ACTION QTY ORDER_TYPE [LIMIT_PRICE]
  uv run python scripts/record_fixture.py get_order_status ORDER_ID

Validates against the pydantic model (via the real client parse_* path — an
unparseable response aborts before anything is written) and scrubs before
writing fixtures/etrade/<endpoint>.<key-params>.<YYYY-MM-DD>.json (T3).

Scrubbing is BY VALUE, not just by key name (etrade-fixtures skill): any known
sensitive value — account IDs discovered anywhere in the payload, consumer
key/secret, access token/secret — is masked wherever it appears, including
embedded inside unrelated strings (E*Trade's real portfolio responses embed
accountIdKey inside `lotsDetails`/`quoteDetails` URLs; a key-name-only scrubber
misses that).
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from etrade_agent.etrade import oauth
from etrade_agent.etrade.client import SANDBOX_BASE_URL, EtradeClient
from etrade_agent.etrade.models import OrderAction, OrderRequest, OrderType

_SCRUBBED = "***SCRUBBED***"
_SENSITIVE_KEY_NAMES = {"accountId", "accountIdKey", "accountKey"}
# Structural fallback (ADR-0002 T3 lesson): E*Trade's OWN canned sandbox data
# embeds an unpredictable account-shaped id inside example "lotsDetails"/
# "details"/"quoteDetails" URLs — a value never known in advance (not Rishi's
# real key, not discoverable by key name in that payload). Scrub any
# "/accounts/<id>/" URL path segment regardless of whether the id is "known".
_ACCOUNT_URL_PATH_RE = re.compile(r"(/accounts/)[^/\s]+")
FIXTURES_DIR = Path("fixtures/etrade")


def _collect_sensitive_values(payload: Any, sensitive_keys: set[str]) -> set[str]:
    """Walk `payload`, returning every string value found nested under a key in
    `sensitive_keys`, anywhere in the structure. Used to build the by-value
    scrub set BEFORE masking (so the embedded-in-a-URL case is caught too)."""
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in sensitive_keys and isinstance(value, str):
                found.add(value)
            found |= _collect_sensitive_values(value, sensitive_keys)
    elif isinstance(payload, list):
        for item in payload:
            found |= _collect_sensitive_values(item, sensitive_keys)
    return found


def scrub_fixture(payload: Any, extra_sensitive_values: set[str]) -> Any:
    """Mask sensitive key names AND any string occurrence of a sensitive value
    (T3). `extra_sensitive_values` carries known secrets from the environment
    (consumer key/secret, access token/secret) not discoverable from the
    payload's own key names."""
    discovered = _collect_sensitive_values(payload, _SENSITIVE_KEY_NAMES)
    sensitive_values = {v for v in (discovered | extra_sensitive_values) if v}
    return _scrub(payload, sensitive_values)


def _scrub(obj: Any, sensitive_values: set[str]) -> Any:
    if isinstance(obj, dict):
        result: dict[str, Any] = {}
        for key, value in obj.items():
            if key in _SENSITIVE_KEY_NAMES or key.startswith("oauth_"):
                result[key] = _SCRUBBED
            else:
                result[key] = _scrub(value, sensitive_values)
        return result
    if isinstance(obj, list):
        return [_scrub(item, sensitive_values) for item in obj]
    if isinstance(obj, str):
        scrubbed = obj
        for value in sensitive_values:
            scrubbed = scrubbed.replace(value, _SCRUBBED)
        scrubbed = _ACCOUNT_URL_PATH_RE.sub(rf"\1{_SCRUBBED}", scrubbed)
        return scrubbed
    return obj


def fixture_filename(endpoint: str, key_params: str, date: str) -> str:
    return f"{endpoint}.{key_params}.{date}.json"


def build_extra_sensitive_values(
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
    account_id_key: str | None,
) -> set[str]:
    """The known-secret set fed to scrub_fixture alongside whatever it discovers
    by key name. account_id_key MUST be included explicitly: it never appears
    under a literal "accountId"/"accountIdKey" key in every endpoint's response
    (get_positions/get_order_status embed it only inside "lotsDetails"/"details"
    URL strings, verified live) — key-based discovery alone misses it there."""
    values = {consumer_key, consumer_secret, token, token_secret}
    if account_id_key:
        values.add(account_id_key)
    return values


class _RecordingSession:
    """Wraps the real signed session; remembers the last raw JSON response so
    the caller can fixture it without duplicating EtradeClient's URL/path
    knowledge (recording always exercises the exact path the client uses)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.last_payload: Any = None

    def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        response = self._inner.get(url, params=params)
        response.raise_for_status()
        self.last_payload = response.json()
        return response

    def post(self, url: str, json: dict[str, Any] | None = None) -> Any:
        response = self._inner.post(url, json=json)
        response.raise_for_status()
        self.last_payload = response.json()
        return response


def _write_fixture(
    endpoint: str, key_params: str, raw_payload: Any, extra_sensitive_values: set[str]
) -> Path:
    scrubbed = scrub_fixture(raw_payload, extra_sensitive_values)
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_DIR / fixture_filename(endpoint, key_params, date)
    path.write_text(json.dumps(scrubbed, indent=2, sort_keys=True) + "\n")
    return path


def _build_order(args: list[str]) -> OrderRequest:
    symbol, action, qty, order_type_str, *rest = args
    order_type = OrderType(order_type_str)
    limit_price = float(rest[0]) if rest else None
    return OrderRequest(
        symbol=symbol,
        order_action=OrderAction(action),
        quantity=int(qty),
        order_type=order_type,
        limit_price=limit_price,
    )


def record(
    endpoint: str,
    args: list[str],
    client: EtradeClient,
    session: _RecordingSession,
    extra_sensitive_values: set[str],
    output: Callable[[str], None] = print,
) -> int:
    """Dispatch one recording. Validation is implicit: every client method below
    parses the response into its pydantic model — an unparseable response raises
    before `_write_fixture` is ever called."""
    if endpoint == "get_quote":
        symbol = args[0]
        client.get_quote(symbol)
        key_params = f"symbol-{symbol}"
    elif endpoint == "get_balances":
        client.get_balances()
        key_params = "default"
    elif endpoint == "get_positions":
        client.get_positions()
        key_params = "default"
    elif endpoint == "preview_order":
        order = _build_order(args)
        client.preview_order(order)
        order_type = order.order_type.value.lower()
        key_params = f"{order.security_type.value}-{order.order_action.value}-{order_type}"
    elif endpoint == "place_order":
        order = _build_order(args)
        _, binding = client.preview_order(order)  # T2: must preview in this run first
        status = client.place_from_binding(binding)
        output(f"placed order id: {status.etrade_order_id}")
        key_params = f"{order.security_type.value}-{order.order_action.value}"
    elif endpoint == "get_order_status":
        order_id = args[0]
        client.get_order_status(order_id)
        key_params = f"orderid-{order_id}"
    else:
        output(f"unknown endpoint: {endpoint}")
        return 1

    path = _write_fixture(endpoint, key_params, session.last_payload, extra_sensitive_values)
    output(f"wrote {path}")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: record_fixture.py <endpoint> [params...]", file=sys.stderr)
        return 1
    endpoint, args = sys.argv[1], sys.argv[2:]

    load_dotenv()
    tokens = oauth.load_tokens(Path("tokens"))
    if tokens is None:
        print("no tokens/ — run: uv run python scripts/oauth_login.py", file=sys.stderr)
        return 1
    consumer_key = os.environ.get("ETRADE_CONSUMER_KEY")
    consumer_secret = os.environ.get("ETRADE_CONSUMER_SECRET")
    if not consumer_key or not consumer_secret:
        print("ETRADE_CONSUMER_KEY/ETRADE_CONSUMER_SECRET missing from .env", file=sys.stderr)
        return 1

    session = _RecordingSession(oauth.signed_session(consumer_key, consumer_secret, tokens))
    client = EtradeClient.connect(
        session, SANDBOX_BASE_URL, account_id_key=os.environ.get("ETRADE_ACCOUNT_ID_KEY")
    )
    # Read back client.account_id_key (not the .env lookup above) so this covers
    # BOTH the explicit-env-var case AND auto-resolution via /v1/accounts/list.
    extra_sensitive_values = build_extra_sensitive_values(
        consumer_key, consumer_secret, tokens.token, tokens.token_secret, client.account_id_key
    )
    return record(endpoint, args, client, session, extra_sensitive_values)


if __name__ == "__main__":
    sys.exit(main())
