"""Render launchd/*.plist.template into ~/Library/LaunchAgents (SPEC §9).

Usage: uv run python scripts/generate_plist.py [--hour H] [--minute M]
Fills {{LABEL}}, {{WORKDIR}}, {{PATH}}, {{HOUR}}, {{MINUTE}} and prints the
launchctl load command. Implemented in Phase 4.
"""

from __future__ import annotations

import sys


def main() -> int:
    raise NotImplementedError("Phase 4 (SPEC §7): render template, write plist, print launchctl")


if __name__ == "__main__":
    sys.exit(main())
