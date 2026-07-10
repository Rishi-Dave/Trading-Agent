"""Record a scrubbed E*Trade sandbox fixture (SPEC §5.4, etrade-fixtures skill).

Usage: uv run python scripts/record_fixture.py <endpoint> [params...]
Validates against the pydantic model and scrubs oauth_* params / account IDs
before writing fixtures/etrade/<endpoint>.<key-params>.<YYYY-MM-DD>.json (T3).
Implemented in Phase 1.
"""

from __future__ import annotations

import sys


def main() -> int:
    raise NotImplementedError("Phase 1 (SPEC §7): record, validate, scrub, write fixture")


if __name__ == "__main__":
    sys.exit(main())
