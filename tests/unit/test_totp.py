"""RFC 6238 TOTP (ADR-0003 point 5, revised post-review): rotating codes
authenticate remote kill-switch/breaker-reset commands over ntfy, replacing
a static reusable token that a code review found was broadcast in cleartext
and permanently replayable. Verified against RFC 6238's own published test
vectors (Appendix B) — not just self-consistency."""

from __future__ import annotations

import base64

from etrade_agent.totp import generate_totp, verify_totp

_RFC6238_SECRET_ASCII = b"12345678901234567890"
_RFC6238_SECRET_B32 = base64.b32encode(_RFC6238_SECRET_ASCII).decode()


def test_generate_totp_matches_rfc6238_test_vectors() -> None:
    # (unix time, expected 8-digit OTP): SHA1, T0=0, step=30s (RFC 6238 Appendix B)
    vectors = [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
    ]
    for unix_time, expected in vectors:
        assert generate_totp(_RFC6238_SECRET_B32, for_time=unix_time, digits=8) == expected


def test_generate_totp_default_is_six_digits() -> None:
    code = generate_totp(_RFC6238_SECRET_B32, for_time=59)
    assert len(code) == 6
    assert code.isdigit()


def test_verify_totp_accepts_the_current_code() -> None:
    code = generate_totp(_RFC6238_SECRET_B32, for_time=1000)
    assert verify_totp(_RFC6238_SECRET_B32, code, for_time=1000) is True


def test_verify_totp_rejects_wrong_code() -> None:
    assert verify_totp(_RFC6238_SECRET_B32, "000000", for_time=1000) is False


def test_verify_totp_tolerates_one_step_of_clock_drift() -> None:
    code = generate_totp(_RFC6238_SECRET_B32, for_time=1000)  # step covering [990, 1020)
    # 40s later falls in the NEXT 30s step; default window=1 tolerates it.
    assert verify_totp(_RFC6238_SECRET_B32, code, for_time=1040) is True


def test_verify_totp_rejects_code_outside_the_window() -> None:
    code = generate_totp(_RFC6238_SECRET_B32, for_time=1000)
    # 90s later is 3 steps away — outside the default +/-1 step window.
    assert verify_totp(_RFC6238_SECRET_B32, code, for_time=1000 + 90) is False


def test_different_secrets_produce_different_codes() -> None:
    other_secret = base64.b32encode(b"a-totally-different-secret").decode()
    assert generate_totp(_RFC6238_SECRET_B32, for_time=1000) != generate_totp(
        other_secret, for_time=1000
    )


def test_generate_random_secret_returns_valid_base32() -> None:
    from etrade_agent.totp import generate_secret

    secret = generate_secret()
    # Must decode cleanly and be usable immediately.
    assert generate_totp(secret, for_time=0)
