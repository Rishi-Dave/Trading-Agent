"""Everything under tests/wall/phase4/ additionally gets the `phase4` marker
(on top of `wall`, auto-applied by the parent tests/wall/conftest.py).

Mirrors tests/wall/phase1/conftest.py and tests/wall/phase3/conftest.py:
isolates the Phase 4 run wall from the day-one-blocking caps wall. CI's
`safety-wall` job runs `-m "wall and not phase1 and not phase3 and not phase4"`
and stays blocking/green regardless of Phase 4 wall state; the new
`phase4-wall` job runs `-m "wall and phase4"`, informational
(continue-on-error) while Phase 4 is open, flipped blocking at phase close
(ADR-0005).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PHASE4_DIR = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if _PHASE4_DIR in Path(item.fspath).parents:
            item.add_marker(pytest.mark.phase4)
