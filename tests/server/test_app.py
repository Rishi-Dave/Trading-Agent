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
from etrade_agent.etrade.client import EtradeClient
from etrade_agent.server.app import (
    Runtime,
    ServerStartupError,
    build_runtime,
    create_app,
    make_run_id,
)
from etrade_agent.server.preview_store import PreviewStore
from etrade_agent.server.safety import ConfiguredSafetyGate
from etrade_agent.store.state import StateStore
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
    # build_runtime (Phase 4/ADR-0005) now attempts a best-effort token
    # renewal on every call; neutralize it here so this stays a live-network-free
    # test (module docstring's "Hermetic" claim) rather than a real HTTPS call
    # to api.etrade.com with garbage fake credentials.
    monkeypatch.setattr("etrade_agent.server.app.oauth.renew_tokens", lambda tokens: tokens)
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


def test_create_app_wires_configured_safety_gate_not_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The most consequential line of Phase 2 (SPEC §7, kickoff prompt):
    PassthroughGate() -> ConfiguredSafetyGate(...). Proven behaviorally: an
    unwhitelisted symbol must refuse via check_preview — BEFORE any live
    E*Trade call — which PassthroughGate could never do (it always allows,
    and the fake creds/session here would otherwise surface as a raw
    connection error, not a clean {"refused": true} payload)."""
    # Same hermeticity note as test_create_app_returns_fastmcp_app_with_six_tools.
    monkeypatch.setattr("etrade_agent.server.app.oauth.renew_tokens", lambda tokens: tokens)
    monkeypatch.setenv("ETRADE_CONSUMER_KEY", "fakekey")
    monkeypatch.setenv("ETRADE_CONSUMER_SECRET", "fakesecret")
    monkeypatch.setenv("ETRADE_ACCOUNT_ID_KEY", "fake-account-key")
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)

    app = create_app(path, tokens_dir=tokens_dir)
    _content, structured = asyncio.run(
        app.call_tool(
            "preview_order",
            {"symbol": "TSLA", "order_action": "BUY", "quantity": 1, "order_type": "MARKET"},
        )
    )

    assert structured == {
        "refused": True,
        "gate": "whitelist",
        "reason": "TSLA is not in an enabled whitelist tier",
        "state": {"symbol": "TSLA", "enabled_symbols": ["AAPL", "SPY"]},
    }


def test_create_app_opens_the_store_next_to_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative store.db_path resolves next to config.toml, not the process
    CWD — keeps test runs (and real runs) from writing into unrelated
    directories."""
    # Same hermeticity note as test_create_app_returns_fastmcp_app_with_six_tools.
    monkeypatch.setattr("etrade_agent.server.app.oauth.renew_tokens", lambda tokens: tokens)
    monkeypatch.setenv("ETRADE_CONSUMER_KEY", "fakekey")
    monkeypatch.setenv("ETRADE_CONSUMER_SECRET", "fakesecret")
    monkeypatch.setenv("ETRADE_ACCOUNT_ID_KEY", "fake-account-key")
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)

    create_app(path, tokens_dir=tokens_dir)

    assert (tmp_path / "trading.db").exists()


def test_build_runtime_returns_runtime_with_all_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPEC §3.1 Step 0 #2 / ADR-0005: build_runtime is the single construction
    path both create_app and the Phase-4 runner use to reach the safety-gated
    tool functions — proven by checking every component create_app wires into
    register_tools is present on the returned Runtime (T1: one enforcement
    setup, never two divergent ones)."""
    monkeypatch.setattr("etrade_agent.server.app.oauth.renew_tokens", lambda tokens: tokens)
    monkeypatch.setenv("ETRADE_CONSUMER_KEY", "fakekey")
    monkeypatch.setenv("ETRADE_CONSUMER_SECRET", "fakesecret")
    monkeypatch.setenv("ETRADE_ACCOUNT_ID_KEY", "fake-account-key")
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)

    rt = build_runtime(path, tokens_dir)

    assert isinstance(rt, Runtime)
    assert rt.config.config_version == 1
    assert isinstance(rt.client, EtradeClient)
    assert isinstance(rt.gate, ConfiguredSafetyGate)
    assert isinstance(rt.store, PreviewStore)
    assert isinstance(rt.state, StateStore)
    assert isinstance(rt.run_id, str) and rt.run_id


def test_build_runtime_attempts_best_effort_token_renewal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPEC §10/ADR-0005: renew_tokens() was built in Phase 1 (ADR-0002) but
    had zero callers until now. build_runtime wires it as best-effort
    idle-timeout recovery so an unattended Phase-4 run doesn't need a human
    every time the token has merely gone idle (2 hr) within the same day."""
    calls: list[oauth.OAuthTokens] = []

    def _spy_renew(tokens: oauth.OAuthTokens) -> oauth.OAuthTokens:
        calls.append(tokens)
        return tokens

    monkeypatch.setattr("etrade_agent.server.app.oauth.renew_tokens", _spy_renew)
    monkeypatch.setenv("ETRADE_CONSUMER_KEY", "fakekey")
    monkeypatch.setenv("ETRADE_CONSUMER_SECRET", "fakesecret")
    monkeypatch.setenv("ETRADE_ACCOUNT_ID_KEY", "fake-account-key")
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)

    build_runtime(path, tokens_dir)

    assert len(calls) == 1
    assert calls[0].token == "faketoken"


def test_build_runtime_returns_runtime_with_a_callable_notify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPEC §9/ADR-0006: build_runtime resolves a NotifyFn (from NTFY_TOPIC
    when the caller doesn't inject one) and exposes it on Runtime — the same
    seam both create_app's gate and the Phase-4 runner reach through."""
    monkeypatch.setattr("etrade_agent.server.app.oauth.renew_tokens", lambda tokens: tokens)
    monkeypatch.setenv("ETRADE_CONSUMER_KEY", "fakekey")
    monkeypatch.setenv("ETRADE_CONSUMER_SECRET", "fakesecret")
    monkeypatch.setenv("ETRADE_ACCOUNT_ID_KEY", "fake-account-key")
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)

    rt = build_runtime(path, tokens_dir)

    assert callable(rt.notify)
    rt.notify("title", "message")  # missing NTFY_TOPIC degrades to a no-op, never raises


def test_build_runtime_passes_the_injected_notify_into_the_gate_and_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T1: build_runtime is the single construction path — the NotifyFn
    passed to ConfiguredSafetyGate must be the SAME instance exposed on
    Runtime, never a second, divergently-built one (ADR-0006, mirrors the
    existing "one gate" discipline ADR-0005 established for the gate itself)."""
    monkeypatch.setattr("etrade_agent.server.app.oauth.renew_tokens", lambda tokens: tokens)
    monkeypatch.setenv("ETRADE_CONSUMER_KEY", "fakekey")
    monkeypatch.setenv("ETRADE_CONSUMER_SECRET", "fakesecret")
    monkeypatch.setenv("ETRADE_ACCOUNT_ID_KEY", "fake-account-key")
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)

    captured: dict[str, object] = {}
    real_init = ConfiguredSafetyGate.__init__

    def _spy_init(self, config, market, state, *, notify=None):  # type: ignore[no-untyped-def]
        captured["notify"] = notify
        real_init(self, config, market, state, notify=notify)

    monkeypatch.setattr(ConfiguredSafetyGate, "__init__", _spy_init)

    def _notify(title: str, message: str) -> None:
        pass

    rt = build_runtime(path, tokens_dir, notify=_notify)

    assert captured["notify"] is _notify
    assert rt.notify is _notify


def test_build_runtime_accepts_an_explicit_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """runner/__main__.py generates a run_id up front (make_run_id()) so a
    startup-failure status report and a successful Runtime share exactly one
    id (Phase 5, SPEC §9) — build_runtime must accept and use it rather than
    always minting its own."""
    monkeypatch.setattr("etrade_agent.server.app.oauth.renew_tokens", lambda tokens: tokens)
    monkeypatch.setenv("ETRADE_CONSUMER_KEY", "fakekey")
    monkeypatch.setenv("ETRADE_CONSUMER_SECRET", "fakesecret")
    monkeypatch.setenv("ETRADE_ACCOUNT_ID_KEY", "fake-account-key")
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)

    rt = build_runtime(path, tokens_dir, run_id="explicit-run-id")

    assert rt.run_id == "explicit-run-id"


def test_make_run_id_returns_a_non_empty_unique_string() -> None:
    first = make_run_id()
    second = make_run_id()

    assert isinstance(first, str) and first
    assert first != second


def test_build_runtime_proceeds_when_renewal_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token dead past the midnight-ET hard expiry (ADR-0002 point 1)
    cannot be renewed non-interactively — renew_tokens() raising is the
    EXPECTED case on a fresh morning, not a startup failure. build_runtime
    must log and proceed with the original tokens; the downstream
    EtradeClient.connect()/signed_session use is the real liveness check on
    a truly dead token."""

    def _failing_renew(tokens: oauth.OAuthTokens) -> oauth.OAuthTokens:
        raise RuntimeError("simulated: renew_access_token returned 401")

    monkeypatch.setattr("etrade_agent.server.app.oauth.renew_tokens", _failing_renew)
    monkeypatch.setenv("ETRADE_CONSUMER_KEY", "fakekey")
    monkeypatch.setenv("ETRADE_CONSUMER_SECRET", "fakesecret")
    monkeypatch.setenv("ETRADE_ACCOUNT_ID_KEY", "fake-account-key")
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)

    rt = build_runtime(path, tokens_dir)  # must not raise

    assert isinstance(rt, Runtime)
