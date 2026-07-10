"""OAuth 1.0a dance unit tests (SPEC §7 Phase 1).

No live network calls: OAuth1Session.fetch_request_token/fetch_access_token are
monkeypatched. Tokens never touch anything but tmp_path/tokens dirs (T3).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from requests_oauthlib import OAuth1Session

from etrade_agent.etrade import oauth


def test_save_and_load_tokens_round_trips(tmp_path: Path) -> None:
    tokens = oauth.OAuthTokens(token="tok-abc", token_secret="sec-xyz")
    oauth.save_tokens(tokens, tmp_path)

    loaded = oauth.load_tokens(tmp_path)

    assert loaded is not None
    assert loaded.token == "tok-abc"
    assert loaded.token_secret == "sec-xyz"


def test_save_tokens_restricts_file_permissions(tmp_path: Path) -> None:
    oauth.save_tokens(oauth.OAuthTokens("t", "s"), tmp_path)

    path = tmp_path / oauth.TOKENS_FILENAME
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


def test_load_tokens_returns_none_when_absent(tmp_path: Path) -> None:
    assert oauth.load_tokens(tmp_path) is None


def test_signed_session_configures_oauth1_header_auth() -> None:
    tokens = oauth.OAuthTokens(token="tok", token_secret="toksec")

    session = oauth.signed_session("ckey", "csecret", tokens)

    assert isinstance(session, OAuth1Session)
    client = session.auth.client
    assert client.client_key == "ckey"
    assert client.client_secret == "csecret"
    assert client.resource_owner_key == "tok"
    assert client.resource_owner_secret == "toksec"
    assert client.signature_type == "AUTH_HEADER"
    assert session.headers["Accept"] == "application/json"


def test_begin_authorization_returns_browser_url(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch_request_token(self: OAuth1Session, url: str) -> dict[str, str]:
        assert url == f"{oauth.OAUTH_BASE_URL}/oauth/request_token"
        return {"oauth_token": "reqtok123", "oauth_token_secret": "reqsec456"}

    monkeypatch.setattr(OAuth1Session, "fetch_request_token", fake_fetch_request_token)

    url = oauth.begin_authorization("ckey", "csecret", sandbox=True)

    assert url == f"{oauth.AUTHORIZE_URL}?key=ckey&token=reqtok123"


def test_complete_authorization_returns_access_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        OAuth1Session,
        "fetch_request_token",
        lambda self, url: {"oauth_token": "reqtok", "oauth_token_secret": "reqsec"},
    )
    monkeypatch.setattr(
        OAuth1Session,
        "fetch_access_token",
        lambda self, url: {"oauth_token": "acctok", "oauth_token_secret": "accsec"},
    )
    oauth.begin_authorization("ckey", "csecret", sandbox=True)

    tokens = oauth.complete_authorization("verifier-code")

    assert tokens.token == "acctok"
    assert tokens.token_secret == "accsec"


def test_complete_authorization_without_begin_raises() -> None:
    oauth._pending = None  # simulate a fresh process that never called begin_authorization

    with pytest.raises(RuntimeError, match="begin_authorization"):
        oauth.complete_authorization("verifier-code")


def test_renew_tokens_calls_renew_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ETRADE_CONSUMER_KEY", "ckey")
    monkeypatch.setenv("ETRADE_CONSUMER_SECRET", "csecret")
    calls = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    def fake_get(self: OAuth1Session, url: str, *args: object, **kwargs: object) -> FakeResponse:
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr(OAuth1Session, "get", fake_get)

    tokens = oauth.OAuthTokens(token="tok", token_secret="toksec")
    renewed = oauth.renew_tokens(tokens)

    assert calls == [f"{oauth.OAUTH_BASE_URL}/oauth/renew_access_token"]
    assert renewed.token == "tok"
    assert renewed.token_secret == "toksec"


def test_renew_tokens_without_consumer_credentials_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ETRADE_CONSUMER_KEY", raising=False)
    monkeypatch.delenv("ETRADE_CONSUMER_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="ETRADE_CONSUMER"):
        oauth.renew_tokens(oauth.OAuthTokens("t", "s"))
