"""MCP server entrypoint (`uv run python -m etrade_agent.server.app`, see .mcp.json).

Startup enforces gate `caps-required` (SPEC §4.2, T5): the server exits nonzero
before registering any tool if config is missing or caps are invalid — `load_config`
is deliberately the FIRST statement in `create_app`, before dotenv/token loading,
so a bad config never reaches those paths (caps wall: test_server_factory_dies_without_caps).

Phase 1 is sandbox-only end to end (sandbox-prod skill): `create_app` refuses any
`environment.mode != "sandbox"` outright — there is no prod code path this phase.
Token loading fails closed (T3): a missing tokens/ directory refuses to start
rather than falling back to any default, with an instruction to run
`scripts/oauth_login.py`.

The safety gate wired here is `ConfiguredSafetyGate` (Phase 2, SPEC §7) — the
cap wall (tests/wall/) forces every §4.2 gate to be real before this swap was
made; `PassthroughGate` (ADR-0002) remains in the tree as a labeled Phase-1
artifact but is no longer reachable from this factory.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from etrade_agent.config import AppConfig, ConfigError, load_config
from etrade_agent.etrade import oauth
from etrade_agent.etrade.client import SANDBOX_BASE_URL, EtradeClient
from etrade_agent.server.preview_store import PreviewStore
from etrade_agent.server.safety import ConfiguredSafetyGate
from etrade_agent.server.tools import register_tools
from etrade_agent.store import db
from etrade_agent.store.state import StateStore

if TYPE_CHECKING:
    from etrade_agent.etrade.client import HttpSession

DEFAULT_CONFIG_PATH = Path("config/config.toml")
DEFAULT_TOKENS_DIR = Path("tokens")


class ServerStartupError(Exception):
    """Startup refused for a reason other than caps (T5's ConfigError covers
    that one specifically, per the caps wall). Sandbox-only mode, missing
    tokens, and missing consumer credentials all raise this."""


def create_app(
    config_path: Path = DEFAULT_CONFIG_PATH, tokens_dir: Path = DEFAULT_TOKENS_DIR
) -> FastMCP:
    """Validate config (T5, must stay first), then build the FastMCP app with
    the six SPEC §5.2 tools registered. Dies (fails closed) before any tool is
    registered if: caps are invalid, mode isn't sandbox, consumer credentials
    are absent, or no OAuth tokens have been recorded yet."""
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

    gate = ConfiguredSafetyGate(config, client, state)
    store = PreviewStore()
    run_id = str(uuid.uuid4())

    app = FastMCP("etrade")
    register_tools(app, client, gate, store, state, config, run_id)
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
