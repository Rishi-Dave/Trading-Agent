"""Everything under tests/wall/phase3/ additionally gets the `phase3` marker
(on top of `wall`, auto-applied by the parent tests/wall/conftest.py).

Mirrors tests/wall/phase1/conftest.py: isolates the Phase 3 pipeline wall from
the day-one-blocking caps wall. CI's `safety-wall` job runs
`-m "wall and not phase1 and not phase3"` and stays blocking/green regardless
of Phase 3 wall state; the new `phase3-wall` job runs `-m "wall and phase3"`,
informational (continue-on-error) while Phase 3 is open, flipped blocking at
phase close (ADR-0004).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PHASE3_DIR = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if _PHASE3_DIR in Path(item.fspath).parents:
            item.add_marker(pytest.mark.phase3)
