"""Everything under tests/wall/ is wall material: auto-apply the `wall` marker.

Wall tests are excluded from default runs (pyproject addopts) and enforced as a
blocking CI job. Walls are never weakened to pass — safety-wall skill.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_WALL_DIR = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # This hook receives the WHOLE session's items even from a sub-conftest —
    # filter to this directory or every test in the repo gets marked `wall`.
    for item in items:
        if _WALL_DIR in Path(item.fspath).parents:
            item.add_marker(pytest.mark.wall)
