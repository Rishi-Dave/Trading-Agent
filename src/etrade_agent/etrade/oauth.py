"""OAuth 1.0a flow for E*Trade (SPEC §7 Phase 1).

Request token → browser authorize → access token, HMAC-SHA1 signing via
requests-oauthlib. Access tokens idle out after 2 hours and hard-expire nightly;
the renewal approach is a Phase 1 Step-0 design question (SPEC §10). Tokens
persist only to the gitignored `tokens/` directory (T3).
"""

from __future__ import annotations

from pathlib import Path

from requests_oauthlib import OAuth1Session


class OAuthTokens:
    """Persisted access token pair. Phase 1 implements load/save/renew."""

    def __init__(self, token: str, token_secret: str) -> None:
        self.token = token
        self.token_secret = token_secret


def begin_authorization(consumer_key: str, consumer_secret: str, sandbox: bool) -> str:
    """Fetch a request token and return the browser authorization URL."""
    raise NotImplementedError("Phase 1 (SPEC §7)")


def complete_authorization(verifier_code: str) -> OAuthTokens:
    """Exchange the verifier code for an access token pair."""
    raise NotImplementedError("Phase 1 (SPEC §7)")


def renew_tokens(tokens: OAuthTokens) -> OAuthTokens:
    """Renew idle-expired tokens (approach per Phase 1 Step-0 ADR, SPEC §10)."""
    raise NotImplementedError("Phase 1 (SPEC §7)")


def load_tokens(tokens_dir: Path) -> OAuthTokens | None:
    """Load persisted tokens from the gitignored tokens/ directory, if present."""
    raise NotImplementedError("Phase 1 (SPEC §7)")


def save_tokens(tokens: OAuthTokens, tokens_dir: Path) -> None:
    """Persist tokens to the gitignored tokens/ directory (T3: never elsewhere)."""
    raise NotImplementedError("Phase 1 (SPEC §7)")


def signed_session(consumer_key: str, consumer_secret: str, tokens: OAuthTokens) -> OAuth1Session:
    """HMAC-SHA1-signed session for API calls."""
    raise NotImplementedError("Phase 1 (SPEC §7)")
