"""Everything under tests/wall/phase1/ additionally gets the `phase1` marker
(on top of `wall`, auto-applied by the parent tests/wall/conftest.py).

This isolates the Phase 1 fixture-replay wall from the day-one-blocking caps
wall (gate `caps-required`, T5): CI's `safety-wall` job runs
`-m "wall and not phase1"` and stays blocking/green regardless of Phase 1 wall
state; the new `phase1-wall` job runs `-m "wall and phase1"`, informational
(continue-on-error) while Phase 1 is open, flipped blocking at phase close.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PHASE1_DIR = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if _PHASE1_DIR in Path(item.fspath).parents:
            item.add_marker(pytest.mark.phase1)
