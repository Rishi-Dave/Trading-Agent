"""RFC 6238 TOTP (Time-based One-Time Password), stdlib only.

Used by scripts/remote_listener.py (ADR-0003 point 5, revised) to authenticate
remote kill-switch/breaker-reset commands sent over ntfy.sh. A code review
found the original design's static, reusable command token was broadcast in
cleartext over the same channel it authenticated — ntfy has no per-subscriber
confidentiality and caches messages, so the token became permanently visible
and replayable after first legitimate use. A TOTP code changes every 30
seconds (RFC 6238 default) and can't be usefully replayed once expired, so
knowledge of a past code doesn't grant a future one. The operator reads the
current code from a standard authenticator app (Google Authenticator, Authy,
1Password, etc.) that was set up once with the shared secret — the code is
never transmitted to compute, only to prove.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time

_DEFAULT_DIGITS = 6
_DEFAULT_PERIOD = 30
_DEFAULT_WINDOW = 1


def generate_secret() -> str:
    """A fresh random base32 shared secret (160 bits, matching the RFC 6238
    Appendix B test-vector length) — set up once into an authenticator app
    and into NTFY_COMMAND_SECRET (.env)."""
    return base64.b32encode(secrets.token_bytes(20)).decode()


def generate_totp(
    secret: str,
    *,
    for_time: float | None = None,
    digits: int = _DEFAULT_DIGITS,
    period: int = _DEFAULT_PERIOD,
) -> str:
    """The TOTP code for `secret` (base32) at `for_time` (default: now)."""
    t = time.time() if for_time is None else for_time
    counter = int(t // period)
    return _hotp(_decode_secret(secret), counter, digits)


def verify_totp(
    secret: str,
    code: str,
    *,
    for_time: float | None = None,
    digits: int = _DEFAULT_DIGITS,
    period: int = _DEFAULT_PERIOD,
    window: int = _DEFAULT_WINDOW,
) -> bool:
    """True iff `code` matches the TOTP for `secret` within `window` time
    steps of `for_time` (default: now) — RFC 6238 recommends a small window
    to tolerate clock drift and network/typing latency. Constant-time
    comparison per step (no early-exit timing signal on which step matched)."""
    t = time.time() if for_time is None else for_time
    accepted = False
    for offset in range(-window, window + 1):
        candidate = generate_totp(
            secret, for_time=t + offset * period, digits=digits, period=period
        )
        if hmac.compare_digest(candidate, code):
            accepted = True
    return accepted


def _decode_secret(secret: str) -> bytes:
    padded = secret.upper()
    padded += "=" * (-len(padded) % 8)
    return base64.b32decode(padded)


def _hotp(secret_bytes: bytes, counter: int, digits: int) -> str:
    """RFC 4226 HOTP — TOTP is HOTP with counter = floor(time / period)."""
    msg = struct.pack(">Q", counter)
    digest = hmac.new(secret_bytes, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = (
        (digest[offset] & 0x7F) << 24
        | (digest[offset + 1] & 0xFF) << 16
        | (digest[offset + 2] & 0xFF) << 8
        | (digest[offset + 3] & 0xFF)
    )
    return str(code_int % (10**digits)).zfill(digits)
