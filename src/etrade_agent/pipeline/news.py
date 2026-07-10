"""News/sentiment source interface (SPEC §6).

v1 implementation (Phase 3) uses Claude Code's built-in WebSearch during decision
runs. The protocol keeps it swappable for a dedicated feed (e.g. Finnhub) if
determinism/fixtures are wanted later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class NewsItem:
    symbol: str
    headline: str
    summary: str
    source: str
    published_at: datetime
    url: str | None = None


class NewsSource(Protocol):
    def headlines(self, symbol: str, since: datetime) -> list[NewsItem]: ...
