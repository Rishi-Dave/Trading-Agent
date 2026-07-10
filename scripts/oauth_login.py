"""Interactive OAuth 1.0a login: request token → browser authorize → access token.

Usage: uv run python scripts/oauth_login.py
Tokens persist to the gitignored tokens/ directory only (T3). Implemented in Phase 1.
"""

from __future__ import annotations

import sys


def main() -> int:
    raise NotImplementedError("Phase 1 (SPEC §7): drive etrade_agent.etrade.oauth interactively")


if __name__ == "__main__":
    sys.exit(main())
