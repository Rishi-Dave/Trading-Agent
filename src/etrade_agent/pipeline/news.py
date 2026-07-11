"""News/sentiment source interface (SPEC §6).

v1 implementation (Phase 3) uses Claude Code's built-in WebSearch during decision
runs. The protocol keeps it swappable for a dedicated feed (e.g. Finnhub) if
determinism/fixtures are wanted later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from etrade_agent.pipeline.llm import LLMClient


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


_HEADLINES_PROMPT = (
    "Use web search to find real news headlines about {symbol} published on or "
    "after {since}. Respond with ONLY a JSON array of objects, each "
    '{{"headline": <string>, "summary": <one-sentence string>, '
    '"published_at": <ISO 8601 datetime string>, "url": <string or null>}}. '
    "If you find nothing, respond with an empty array []."
)


@dataclass
class WebSearchNewsSource:
    """v1 `NewsSource` (SPEC §6): prompts the injected `LLMClient` with
    `allowed_tools=["WebSearch"]` and parses the response into `NewsItem`s.
    Malformed or non-JSON model output yields zero items rather than raising
    — a news source that can't find anything usable should look empty to
    callers, not crash the run. Kept swappable: any other `NewsSource`
    (e.g. a Finnhub-backed one) can replace this without touching analyst
    steps, which depend only on the Protocol.
    """

    llm: LLMClient

    def headlines(self, symbol: str, since: datetime) -> list[NewsItem]:
        prompt = _HEADLINES_PROMPT.format(symbol=symbol, since=since.isoformat())
        raw = self.llm.complete(prompt, allowed_tools=["WebSearch"])
        return _parse_news_items(raw, symbol=symbol)


def _parse_news_items(raw: str, *, symbol: str) -> list[NewsItem]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    items: list[NewsItem] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        headline = entry.get("headline")
        summary = entry.get("summary")
        published_raw = entry.get("published_at")
        if not isinstance(headline, str) or not isinstance(summary, str):
            continue
        if not isinstance(published_raw, str):
            continue
        try:
            published_at = datetime.fromisoformat(published_raw)
        except ValueError:
            continue
        url = entry.get("url")
        items.append(
            NewsItem(
                symbol=symbol,
                headline=headline,
                summary=summary,
                source="web-search",
                published_at=published_at,
                url=url if isinstance(url, str) else None,
            )
        )
    return items
