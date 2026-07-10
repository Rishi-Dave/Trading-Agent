"""Fixture scrubbing (SPEC §5.4, T3, etrade-fixtures skill).

The scrubber must catch account-identifying values wherever they appear —
including embedded inside unrelated string fields (e.g. a URL) — not just when
they sit directly under a sensitive key name. That gap is exactly what leaked a
real accountIdKey during live Phase 1 development (embedded in `lotsDetails`/
`quoteDetails` URLs in a real portfolio response).

scripts/ isn't on pythonpath (ADR-0001); load by file path like test_oauth_login.py.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "record_fixture.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("record_fixture_script", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def record_fixture() -> ModuleType:
    return _load_module()


def test_collect_sensitive_values_finds_nested_key_values(record_fixture: ModuleType) -> None:
    payload = {
        "PortfolioResponse": {
            "AccountPortfolio": [{"accountId": "12345678", "Position": []}],
        }
    }

    found = record_fixture._collect_sensitive_values(payload, {"accountId", "accountIdKey"})

    assert found == {"12345678"}


def test_scrub_masks_value_embedded_in_unrelated_string_field(record_fixture: ModuleType) -> None:
    # This is the exact real-world shape that leaked: accountIdKey appears both
    # as a normal field AND embedded inside a URL under an unrelated key.
    payload = {
        "PortfolioResponse": {
            "AccountPortfolio": [
                {
                    "accountId": "synthetic-acct-key-1",
                    "Position": [
                        {
                            "lotsDetails": "https://apisb.etrade.com/v1/accounts/synthetic-acct-key-1/portfolio/1",
                            "quoteDetails": "https://apisb.etrade.com/v1/market/quote/BR",
                        }
                    ],
                }
            ]
        }
    }

    scrubbed = record_fixture.scrub_fixture(payload, extra_sensitive_values={"csecret-value"})

    dumped = str(scrubbed)
    assert "synthetic-acct-key-1" not in dumped
    account = scrubbed["PortfolioResponse"]["AccountPortfolio"][0]
    assert account["accountId"] == "***SCRUBBED***"
    lots_details = account["Position"][0]["lotsDetails"]
    assert "***SCRUBBED***" in lots_details
    assert "/portfolio/1" in lots_details  # non-sensitive suffix preserved


def test_scrub_masks_extra_sensitive_values_like_env_secrets(record_fixture: ModuleType) -> None:
    payload = {"note": "consumer key was CKEY123 in this debug string"}

    scrubbed = record_fixture.scrub_fixture(payload, extra_sensitive_values={"CKEY123"})

    assert "CKEY123" not in scrubbed["note"]


def test_scrub_strips_oauth_prefixed_keys_defensively(record_fixture: ModuleType) -> None:
    payload = {"oauth_token": "should-not-appear", "quoteStatus": "REALTIME"}

    scrubbed = record_fixture.scrub_fixture(payload, extra_sensitive_values=set())

    assert scrubbed["oauth_token"] == "***SCRUBBED***"
    assert scrubbed["quoteStatus"] == "REALTIME"


def test_scrub_leaves_non_sensitive_data_untouched(record_fixture: ModuleType) -> None:
    payload = {"symbol": "SPY", "bid": 411.2, "nested": {"volume": 100}}

    scrubbed = record_fixture.scrub_fixture(payload, extra_sensitive_values=set())

    assert scrubbed == payload


def test_scrub_masks_unknown_account_id_in_url_path_structurally(
    record_fixture: ModuleType,
) -> None:
    # Real incident (this session): E*Trade's OWN canned sandbox data embeds a
    # DIFFERENT, unpredictable account-shaped id inside example "lotsDetails"/
    # "details"/"quoteDetails" URLs — one we can't know in advance to add to
    # extra_sensitive_values (it isn't Rishi's real key; it's E*Trade's fixture
    # demo constant). Scrubbing must catch ANY "/accounts/<id>/" URL segment
    # structurally, not just values we already know to look for.
    payload = {
        "lotsDetails": "https://apisb.etrade.com/v1/accounts/SomeUnknownDemoKey123/portfolio/1",
        "quoteDetails": "https://apisb.etrade.com/v1/market/quote/BR",
    }

    scrubbed = record_fixture.scrub_fixture(payload, extra_sensitive_values=set())

    assert "SomeUnknownDemoKey123" not in scrubbed["lotsDetails"]
    assert "/accounts/***SCRUBBED***/portfolio/1" in scrubbed["lotsDetails"]
    assert scrubbed["quoteDetails"] == "https://apisb.etrade.com/v1/market/quote/BR"


def test_fixture_filename_format(record_fixture: ModuleType) -> None:
    name = record_fixture.fixture_filename("get_quote", "symbol-SPY", "2026-07-15")

    assert name == "get_quote.symbol-SPY.2026-07-15.json"


def test_build_extra_sensitive_values_includes_account_id_key(record_fixture: ModuleType) -> None:
    # Real incident (this session): accountIdKey never appears under a literal
    # "accountId"/"accountIdKey" key in get_positions/get_order_status responses
    # — only embedded inside "lotsDetails"/"details" URL strings. scrub_fixture's
    # key-based discovery can't find it there; it MUST be passed in explicitly.
    values = record_fixture.build_extra_sensitive_values(
        consumer_key="ckey",
        consumer_secret="csecret",
        token="tok",
        token_secret="toksecret",
        account_id_key="the-real-account-id-key",
    )

    assert values == {"ckey", "csecret", "tok", "toksecret", "the-real-account-id-key"}


def test_build_extra_sensitive_values_omits_account_id_key_when_none(
    record_fixture: ModuleType,
) -> None:
    values = record_fixture.build_extra_sensitive_values(
        consumer_key="ckey",
        consumer_secret="csecret",
        token="tok",
        token_secret="toksecret",
        account_id_key=None,
    )

    assert values == {"ckey", "csecret", "tok", "toksecret"}
