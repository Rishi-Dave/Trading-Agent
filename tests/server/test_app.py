"""FastMCP app factory (SPEC §7 Phase 1). `create_app` must keep `load_config()`
as its first statement — the caps wall (tests/wall/test_caps_wall.py) depends on
ConfigError being raised before anything else exists. Beyond that: sandbox-only
(Phase 1, sandbox-prod skill), fail-closed on missing tokens (T3), and returns a
FastMCP app with all six tools registered.

Hermetic: env vars are monkeypatched (load_dotenv has override=False, verified
against the installed dotenv package, so these never leak from the real .env),
and ETRADE_ACCOUNT_ID_KEY is always set so EtradeClient.connect never makes a
live /v1/accounts/list call.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from etrade_agent.config import ConfigError
from etrade_agent.etrade import oauth
from etrade_agent.server.app import ServerStartupError, create_app
from tests.conftest import VALID_CONFIG_TOML


def _write_config(tmp_path: Path, mode: str = "sandbox") -> Path:
    text = VALID_CONFIG_TOML.replace('mode = "sandbox"', f'mode = "{mode}"')
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def _save_fake_tokens(tokens_dir: Path) -> None:
    oauth.save_tokens(oauth.OAuthTokens("faketoken", "fakesecret"), tokens_dir)


def test_create_app_dies_on_missing_caps_before_anything_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No consumer key/secret, no tokens dir — if create_app got past load_config
    # it would raise ServerStartupError instead. Getting ConfigError specifically
    # proves load_config ran first (the wall test's exact dependency).
    monkeypatch.delenv("ETRADE_CONSUMER_KEY", raising=False)
    monkeypatch.delenv("ETRADE_CONSUMER_SECRET", raising=False)
    path = tmp_path / "config.toml"
    path.write_text('config_version = 1\n[environment]\nmode = "sandbox"\n')

    with pytest.raises(ConfigError):
        create_app(path, tokens_dir=tmp_path / "notokens")


def test_create_app_refuses_prod_mode(tmp_path: Path) -> None:
    path = _write_config(tmp_path, mode="prod")

    with pytest.raises(ServerStartupError, match="sandbox"):
        create_app(path, tokens_dir=tmp_path / "notokens")


def test_create_app_fails_closed_without_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ETRADE_CONSUMER_KEY", "fakekey")
    monkeypatch.setenv("ETRADE_CONSUMER_SECRET", "fakesecret")
    path = _write_config(tmp_path)

    with pytest.raises(ServerStartupError, match="oauth_login"):
        create_app(path, tokens_dir=tmp_path / "notokens")


def test_create_app_requires_consumer_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # find_dotenv()'s default search walks up from app.py's own (fixed, in-repo)
    # source location, not from cwd — it would always rediscover this repo's
    # real .env and refill the vars we just deleted. Neutralize load_dotenv so
    # "credentials absent" is actually hermetic, independent of what's on disk.
    monkeypatch.setattr("etrade_agent.server.app.load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("ETRADE_CONSUMER_KEY", raising=False)
    monkeypatch.delenv("ETRADE_CONSUMER_SECRET", raising=False)
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)

    with pytest.raises(ServerStartupError, match="ETRADE_CONSUMER"):
        create_app(path, tokens_dir=tokens_dir)


def test_create_app_fails_closed_on_ambiguous_account_without_leaking_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Code-review finding: EtradeClient.connect() raises a bare ValueError when
    # account auto-resolution is ambiguous (a real scenario per ADR-0002 §6).
    # create_app only caught ConfigError/ServerStartupError, so this propagated
    # as an unhandled traceback instead of failing closed like every other
    # startup check. Must become a ServerStartupError with no raw account ids.
    # Same hermeticity gotcha as test_create_app_requires_consumer_credentials:
    # load_dotenv() inside create_app would rediscover this repo's real .env
    # (which has a real ETRADE_ACCOUNT_ID_KEY) and refill the var we just
    # deleted, skipping the ambiguous-resolution path entirely. Neutralize it.
    monkeypatch.setattr("etrade_agent.server.app.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("ETRADE_CONSUMER_KEY", "fakekey")
    monkeypatch.setenv("ETRADE_CONSUMER_SECRET", "fakesecret")
    monkeypatch.delenv("ETRADE_ACCOUNT_ID_KEY", raising=False)
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)

    ambiguous_accounts = {
        "AccountListResponse": {
            "Accounts": {
                "Account": [
                    {
                        "accountId": "87654321",
                        "accountIdKey": "a",
                        "accountType": "INDIVIDUAL",
                        "accountStatus": "ACTIVE",
                    },
                    {
                        "accountId": "87654322",
                        "accountIdKey": "b",
                        "accountType": "INDIVIDUAL",
                        "accountStatus": "ACTIVE",
                    },
                ]
            }
        }
    }

    class FakeResponse:
        def __init__(self, payload: object) -> None:
            self._payload = payload

        def json(self) -> object:
            return self._payload

        def raise_for_status(self) -> None:
            return None

    class FakeSession:
        def get(self, url: str, params: dict[str, object] | None = None) -> FakeResponse:
            return FakeResponse(ambiguous_accounts)

    monkeypatch.setattr(
        "etrade_agent.server.app.oauth.signed_session", lambda *a, **k: FakeSession()
    )

    with pytest.raises(ServerStartupError) as exc_info:
        create_app(path, tokens_dir=tokens_dir)

    message = str(exc_info.value)
    assert "87654321" not in message
    assert "87654322" not in message


def test_create_app_returns_fastmcp_app_with_six_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ETRADE_CONSUMER_KEY", "fakekey")
    monkeypatch.setenv("ETRADE_CONSUMER_SECRET", "fakesecret")
    monkeypatch.setenv("ETRADE_ACCOUNT_ID_KEY", "fake-account-key")
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)

    app = create_app(path, tokens_dir=tokens_dir)

    registered = {t.name for t in asyncio.run(app.list_tools())}
    assert registered == {
        "get_quote",
        "get_positions",
        "get_balances",
        "preview_order",
        "place_order",
        "get_order_status",
    }
