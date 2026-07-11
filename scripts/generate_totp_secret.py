"""One-time TOTP secret setup for scripts/remote_listener.py (ADR-0003 point
5). Prints a fresh random base32 secret and an otpauth:// URI for adding to a
standard authenticator app (Google Authenticator, Authy, 1Password, etc.) —
set up the app FIRST, then save the printed secret into NTFY_COMMAND_SECRET
(.env). This is the one place the secret is meant to be seen; it is never
written anywhere else — not to a log file, not to any other script (T3).

Usage: uv run python scripts/generate_totp_secret.py [--account NAME]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from urllib.parse import quote

from etrade_agent.totp import generate_secret

_ISSUER = "etrade-agent"


def _run(
    account: str,
    *,
    secret_factory: Callable[[], str] = generate_secret,
    output: Callable[[str], None] = print,
) -> int:
    secret = secret_factory()
    uri = (
        f"otpauth://totp/{quote(_ISSUER)}:{quote(account)}?secret={secret}&issuer={quote(_ISSUER)}"
    )

    output("Set up a TOTP authenticator app (Google Authenticator, Authy, 1Password, etc.):")
    output(f"  Account label: {account}")
    output(f"  1. Add a new account manually with this secret: {secret}")
    output(f"     (or paste this URI if your app supports it):\n     {uri}")
    output("  2. Save the same secret into .env:")
    output(f"     NTFY_COMMAND_SECRET={secret}")
    output("  3. Never commit .env or share this secret (T3) — it is not printed anywhere again.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a TOTP secret for scripts/remote_listener.py (ADR-0003 point 5)."
    )
    parser.add_argument(
        "--account", default="etrade-agent-remote", help="Label shown in the authenticator app."
    )
    args = parser.parse_args()
    return _run(args.account)


if __name__ == "__main__":
    sys.exit(main())
