"""OAuth 1.0a flow for E*Trade (SPEC §7 Phase 1).

Request token → browser authorize → access token, HMAC-SHA1 signing via
requests-oauthlib. Access tokens idle out after 2 hours and hard-expire nightly;
Phase 1 Step-0 ADR (docs/decisions/0002) chose an interactive re-dance per run —
`renew_tokens` only recovers an idle-but-same-day token, it cannot survive the
midnight expiry. Tokens persist only to the gitignored `tokens/` directory (T3).

OAuth host note (ADR-0002): E*Trade's OAuth endpoints (`request_token`,
`access_token`, `renew_access_token`) live ONLY on the shared `api.etrade.com` /
`us.etrade.com` hosts — there is no sandbox OAuth host. Sandbox-ness is selected
by the sandbox *consumer key*, not this URL. These mint tokens; they are not the
money path (that stays `apisb.etrade.com`, gated by `environment.mode`, see
`etrade/client.py`). Do not "fix" these to `apisb` — that host does not exist.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from requests_oauthlib import OAuth1Session

OAUTH_BASE_URL = "https://api.etrade.com"
AUTHORIZE_URL = "https://us.etrade.com/e/t/etws/authorize"
_REQUEST_TOKEN_URL = f"{OAUTH_BASE_URL}/oauth/request_token"
_ACCESS_TOKEN_URL = f"{OAUTH_BASE_URL}/oauth/access_token"
_RENEW_TOKEN_URL = f"{OAUTH_BASE_URL}/oauth/renew_access_token"

TOKENS_FILENAME = "etrade.tokens.json"


class OAuthTokens:
    """Persisted access token pair. Phase 1 implements load/save/renew."""

    def __init__(self, token: str, token_secret: str) -> None:
        self.token = token
        self.token_secret = token_secret


@dataclass
class _PendingRequestToken:
    """Request-token half of the dance, held between begin_/complete_authorization.

    Interactive-login-only state (SPEC §10 ADR-0002): a single dance runs at a
    time in one `oauth_login.py` process. Never persisted, never logged (T3).
    """

    consumer_key: str
    consumer_secret: str
    token: str
    token_secret: str


_pending: _PendingRequestToken | None = None


def begin_authorization(consumer_key: str, consumer_secret: str, sandbox: bool) -> str:
    """Fetch a request token and return the browser authorization URL.

    `sandbox` does not change the OAuth host (see module docstring) — sandbox
    vs. prod is selected by which consumer key is passed in. Kept in the
    signature so callers stay explicit about which credentials they intend.
    """
    global _pending
    session = OAuth1Session(consumer_key, client_secret=consumer_secret, callback_uri="oob")
    token_data = session.fetch_request_token(_REQUEST_TOKEN_URL)
    _pending = _PendingRequestToken(
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        token=token_data["oauth_token"],
        token_secret=token_data["oauth_token_secret"],
    )
    return f"{AUTHORIZE_URL}?key={quote(consumer_key)}&token={quote(_pending.token)}"


def complete_authorization(verifier_code: str) -> OAuthTokens:
    """Exchange the verifier code for an access token pair."""
    if _pending is None:
        raise RuntimeError("begin_authorization must be called before complete_authorization")
    session = OAuth1Session(
        _pending.consumer_key,
        client_secret=_pending.consumer_secret,
        resource_owner_key=_pending.token,
        resource_owner_secret=_pending.token_secret,
        verifier=verifier_code,
    )
    token_data = session.fetch_access_token(_ACCESS_TOKEN_URL)
    return OAuthTokens(token_data["oauth_token"], token_data["oauth_token_secret"])


def renew_tokens(tokens: OAuthTokens) -> OAuthTokens:
    """Renew idle-expired tokens (approach per Phase 1 Step-0 ADR, SPEC §10).

    Only recovers a same-day idle timeout (2 hr); a token dead past midnight ET
    needs the full begin_/complete_authorization dance, not this.
    """
    consumer_key = os.environ.get("ETRADE_CONSUMER_KEY")
    consumer_secret = os.environ.get("ETRADE_CONSUMER_SECRET")
    if not consumer_key or not consumer_secret:
        raise RuntimeError("ETRADE_CONSUMER_KEY/ETRADE_CONSUMER_SECRET not set in environment")
    session = signed_session(consumer_key, consumer_secret, tokens)
    response = session.get(_RENEW_TOKEN_URL)
    response.raise_for_status()
    return tokens  # E*Trade reactivates the same pair; no new secret is issued


def load_tokens(tokens_dir: Path) -> OAuthTokens | None:
    """Load persisted tokens from the gitignored tokens/ directory, if present."""
    path = tokens_dir / TOKENS_FILENAME
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return OAuthTokens(data["token"], data["token_secret"])


def save_tokens(tokens: OAuthTokens, tokens_dir: Path) -> None:
    """Persist tokens to the gitignored tokens/ directory (T3: never elsewhere)."""
    tokens_dir.mkdir(parents=True, exist_ok=True)
    path = tokens_dir / TOKENS_FILENAME
    path.write_text(json.dumps({"token": tokens.token, "token_secret": tokens.token_secret}))
    path.chmod(0o600)


def signed_session(consumer_key: str, consumer_secret: str, tokens: OAuthTokens) -> OAuth1Session:
    """HMAC-SHA1-signed session for API calls."""
    session = OAuth1Session(
        consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=tokens.token,
        resource_owner_secret=tokens.token_secret,
        signature_type="AUTH_HEADER",
    )
    session.headers["Accept"] = "application/json"
    return session
