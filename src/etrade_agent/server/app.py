"""MCP server entrypoint (`uv run python -m etrade_agent.server.app`, see .mcp.json).

Startup enforces gate `caps-required` (SPEC §4.2, T5): the server exits nonzero
before registering any tool if config is missing or caps are invalid — `load_config`
is deliberately the FIRST statement in `build_runtime`, before dotenv/token loading,
so a bad config never reaches those paths (caps wall: test_server_factory_dies_without_caps).

Phase 1 is sandbox-only end to end (sandbox-prod skill): startup refuses any
`environment.mode != "sandbox"` outright — there is no prod code path this phase.
Token loading fails closed (T3): a missing tokens/ directory refuses to start
rather than falling back to any default, with an instruction to run
`scripts/oauth_login.py`.

The safety gate wired here is `ConfiguredSafetyGate` (Phase 2, SPEC §7) — the
cap wall (tests/wall/) forces every §4.2 gate to be real before this swap was
made; `PassthroughGate` (ADR-0002) remains in the tree as a labeled Phase-1
artifact but is no longer reachable from this factory.

`build_runtime` (Phase 4, ADR-0005/SPEC §3.1 Step 0 #2) is the single object-
construction path both this MCP server (`create_app`) and the Phase-4 runner
(`runner/decision_run.py`) use to reach the safety-gated tool functions — one
enforcement setup, never two divergently-built copies (T1). `create_app` is a
thin FastMCP wrapper around it.
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from etrade_agent import logs
from etrade_agent.config import AppConfig, ConfigError, load_config
from etrade_agent.etrade import oauth
from etrade_agent.etrade.client import SANDBOX_BASE_URL, EtradeClient
from etrade_agent.notify.ntfy import NotifyFn, build_notify
from etrade_agent.server.preview_store import PreviewStore
from etrade_agent.server.safety import ConfiguredSafetyGate
from etrade_agent.server.tools import register_tools
from etrade_agent.store import db
from etrade_agent.store.state import StateStore

if TYPE_CHECKING:
    from etrade_agent.etrade.client import HttpSession

DEFAULT_CONFIG_PATH = Path("config/config.toml")
DEFAULT_TOKENS_DIR = Path("tokens")

_AGENT_ID = "etrade-server"


class ServerStartupError(Exception):
    """Startup refused for a reason other than caps (T5's ConfigError covers
    that one specifically, per the caps wall). Sandbox-only mode, missing
    tokens, and missing consumer credentials all raise this."""


def _default_notify(title: str, message: str) -> None:
    """A safe no-op default for Runtime.notify. build_runtime (the only
    production construction path) always passes a real NotifyFn; this exists
    so a plain `Runtime(...)` test construction that doesn't care about
    notifications doesn't need to supply one."""
    return None


@dataclass(frozen=True)
class Runtime:
    """Everything a safety-gated caller needs to reach preview_order/
    place_order (SPEC §3.1 Step 0 #2, ADR-0005): built once, by build_runtime,
    so the interactive MCP server and the Phase-4 runner enforce through the
    identical ConfiguredSafetyGate/EtradeClient/StateStore — never a second,
    divergently-constructed copy (T1)."""

    config: AppConfig
    client: EtradeClient
    gate: ConfiguredSafetyGate
    store: PreviewStore
    state: StateStore
    run_id: str
    notify: NotifyFn = _default_notify  # the SAME instance wired into `gate` (ADR-0006)


def _best_effort_renew(tokens: oauth.OAuthTokens) -> oauth.OAuthTokens:
    """Idle-timeout recovery only (ADR-0005, SPEC §10; renew_tokens() itself
    is Phase 1 / ADR-0002 point 1). renew_tokens() cannot survive the
    midnight-ET hard expiry, so a failure here is the EXPECTED case on a
    fresh morning, not a startup failure — it's caught and logged, and the
    caller proceeds with the original tokens. The downstream
    EtradeClient.connect()/signed_session use is the real liveness check
    that fails closed on a token that is genuinely dead."""
    try:
        return oauth.renew_tokens(tokens)
    except Exception as exc:  # best-effort: a failure here just means "keep the existing tokens"
        logs.log(
            _AGENT_ID,
            "warning",
            "token renewal failed (idle-timeout recovery attempt); proceeding with existing tokens",
            error=str(exc),
        )
        return tokens


def make_run_id() -> str:
    """One run_id, minted once. Callers that need it before build_runtime can
    succeed (runner/__main__.py's startup-failure status reports, Phase 5,
    SPEC §9) call this directly and pass the result into build_runtime's
    `run_id=` so a failed and a successful run alike are identifiable by the
    same id, never two divergently-minted ones."""
    return str(uuid.uuid4())


def build_runtime(
    config_path: Path = DEFAULT_CONFIG_PATH,
    tokens_dir: Path = DEFAULT_TOKENS_DIR,
    *,
    notify: NotifyFn | None = None,
    run_id: str | None = None,
) -> Runtime:
    """Validate config (T5, must stay first), then construct every object a
    safety-gated caller needs. Dies (fails closed) before anything is built
    if: caps are invalid, mode isn't sandbox, consumer credentials are
    absent, or no OAuth tokens have been recorded yet.

    `notify` defaults to `build_notify(NTFY_TOPIC)` (SPEC §9) when the caller
    doesn't inject one, and is wired into BOTH the returned Runtime and the
    ConfiguredSafetyGate it constructs — one NotifyFn instance, never a
    second, divergently-built one (ADR-0006, mirrors the "one gate" discipline
    ADR-0005 established). This is what lets a loss-breaker trip notify
    regardless of caller: the runner's execute_decisions loop, or a manual
    .mcp.json place_order through create_app's interactive server."""
    config: AppConfig = load_config(config_path)

    if config.environment.mode != "sandbox":
        raise ServerStartupError(
            "Phase 1 is sandbox-only (SPEC §7); prod requires the sandbox-prod skill"
        )

    load_dotenv()
    consumer_key = os.environ.get("ETRADE_CONSUMER_KEY")
    consumer_secret = os.environ.get("ETRADE_CONSUMER_SECRET")
    if not consumer_key or not consumer_secret:
        raise ServerStartupError("ETRADE_CONSUMER_KEY/ETRADE_CONSUMER_SECRET missing from .env")

    tokens = oauth.load_tokens(tokens_dir)
    if tokens is None:
        raise ServerStartupError(
            f"no tokens in {tokens_dir}/ — run: uv run python scripts/oauth_login.py"
        )

    tokens = _best_effort_renew(tokens)

    session = oauth.signed_session(consumer_key, consumer_secret, tokens)
    # OAuth1Session structurally satisfies HttpSession at runtime (we only ever
    # call .get(url, params=...)/.post(url, json=...)), but requests' stubs are
    # written for full Session generality and don't match the simplified
    # Protocol's positional signature — hence the explicit, documented cast.
    try:
        client = EtradeClient.connect(
            cast("HttpSession", session),
            SANDBOX_BASE_URL,
            account_id_key=os.environ.get("ETRADE_ACCOUNT_ID_KEY"),
        )
    except ValueError as exc:
        # Ambiguous account auto-resolution (client.py::_select_brokerage_account)
        # raises a bare ValueError — must fail closed like every other startup
        # check here, not propagate as an unhandled traceback (code-review
        # finding: that traceback could otherwise land in launchd's stderr log,
        # SPEC §9). The ValueError's own message is already redacted (no raw
        # account ids); re-wrapping keeps that guarantee end-to-end.
        raise ServerStartupError(str(exc)) from exc
    db_path = Path(config.store.db_path)
    if not db_path.is_absolute():
        # Keep the DB next to the config it was loaded from — real runs land
        # at config/trading.db (gitignored, *.db), test runs stay isolated
        # under their own tmp_path, never the real repo's config/ directory.
        db_path = config_path.parent / db_path
    state = StateStore(db.connect(db_path))

    resolved_notify = notify if notify is not None else build_notify(os.environ.get("NTFY_TOPIC"))
    gate = ConfiguredSafetyGate(config, client, state, notify=resolved_notify)
    store = PreviewStore()
    resolved_run_id = run_id if run_id is not None else make_run_id()

    return Runtime(
        config=config,
        client=client,
        gate=gate,
        store=store,
        state=state,
        run_id=resolved_run_id,
        notify=resolved_notify,
    )


def create_app(
    config_path: Path = DEFAULT_CONFIG_PATH, tokens_dir: Path = DEFAULT_TOKENS_DIR
) -> FastMCP:
    """Build the FastMCP app with the six SPEC §5.2 tools registered, via
    build_runtime — the shared runtime-construction path (ADR-0005)."""
    rt = build_runtime(config_path, tokens_dir)
    app = FastMCP("etrade")
    register_tools(app, rt.client, rt.gate, rt.store, rt.state, rt.config, rt.run_id)
    return app


def main() -> int:
    try:
        app = create_app()
    except ConfigError as exc:
        print(f"refusing to start (caps-required, SPEC §4.2): {exc}", file=sys.stderr)
        return 1
    except ServerStartupError as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 1
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
