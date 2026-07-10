"""Interactive OAuth 1.0a login: request token → browser authorize → access token.

Usage: uv run python scripts/oauth_login.py
Tokens persist to the gitignored tokens/ directory only (T3). Run this once each
morning before market open (ADR-0002: E*Trade access tokens hard-expire at
midnight ET, so a fresh browser dance is required daily regardless of caching).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

from etrade_agent.etrade import oauth

TOKENS_DIR = Path("tokens")


def _run(
    consumer_key: str | None,
    consumer_secret: str | None,
    tokens_dir: Path,
    prompt: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> int:
    """Drive the dance. Never prints token/secret values (T3) — only the request
    token embedded in the authorize URL, which is safe (useless without the
    consumer secret and the browser-side session)."""
    if not consumer_key or not consumer_secret:
        output(
            "ETRADE_CONSUMER_KEY / ETRADE_CONSUMER_SECRET missing from .env — "
            "cannot start the OAuth dance (SPEC §10)."
        )
        return 1

    url = oauth.begin_authorization(consumer_key, consumer_secret, sandbox=True)
    output(f"Open this URL, log in, and accept access:\n{url}")
    verifier = prompt("Paste the verifier code E*Trade shows you: ").strip()
    if not verifier:
        output("No verifier entered — aborting.")
        return 1

    tokens = oauth.complete_authorization(verifier)
    oauth.save_tokens(tokens, tokens_dir)
    output(f"Saved access tokens to {tokens_dir}/ (gitignored). The MCP server can start now.")
    return 0


def main() -> int:
    load_dotenv()
    return _run(
        os.environ.get("ETRADE_CONSUMER_KEY"),
        os.environ.get("ETRADE_CONSUMER_SECRET"),
        TOKENS_DIR,
    )


if __name__ == "__main__":
    sys.exit(main())
