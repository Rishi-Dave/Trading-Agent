"""WebSearchNewsSource (SPEC §6 v1 NewsSource) — parses the injected
LLMClient's response into NewsItems; malformed output yields an empty list
rather than raising."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from etrade_agent.pipeline.news import WebSearchNewsSource


@dataclass
class StubLLMClient:
    response: str = "[]"
    calls: list[tuple[str, tuple[str, ...] | None]] = field(default_factory=list)

    def complete(self, prompt: str, *, allowed_tools: list[str] | None = None) -> str:
        self.calls.append((prompt, tuple(allowed_tools) if allowed_tools else None))
        return self.response


def test_requests_websearch_tool() -> None:
    llm = StubLLMClient(response="[]")
    source = WebSearchNewsSource(llm=llm)

    source.headlines("SPY", datetime.now(UTC))

    assert llm.calls[0][1] == ("WebSearch",)


def test_parses_a_valid_json_array() -> None:
    llm = StubLLMClient(
        response=json.dumps(
            [
                {
                    "headline": "Markets rally",
                    "summary": "Indices climbed.",
                    "published_at": "2026-07-10T12:00:00+00:00",
                    "url": "https://example.com/a",
                }
            ]
        )
    )
    source = WebSearchNewsSource(llm=llm)

    items = source.headlines("SPY", datetime.now(UTC))

    assert len(items) == 1
    assert items[0].symbol == "SPY"
    assert items[0].headline == "Markets rally"
    assert items[0].source == "web-search"
    assert items[0].url == "https://example.com/a"


def test_empty_array_yields_no_items() -> None:
    llm = StubLLMClient(response="[]")
    source = WebSearchNewsSource(llm=llm)

    assert source.headlines("SPY", datetime.now(UTC)) == []


def test_non_json_response_yields_no_items() -> None:
    llm = StubLLMClient(response="not json at all")
    source = WebSearchNewsSource(llm=llm)

    assert source.headlines("SPY", datetime.now(UTC)) == []


def test_non_list_json_response_yields_no_items() -> None:
    llm = StubLLMClient(response=json.dumps({"headline": "not a list"}))
    source = WebSearchNewsSource(llm=llm)

    assert source.headlines("SPY", datetime.now(UTC)) == []


def test_entries_missing_required_fields_are_skipped() -> None:
    llm = StubLLMClient(
        response=json.dumps(
            [
                {"headline": "ok", "summary": "s", "published_at": "2026-07-10T00:00:00+00:00"},
                {"headline": "missing summary", "published_at": "2026-07-10T00:00:00+00:00"},
                {"summary": "missing headline", "published_at": "2026-07-10T00:00:00+00:00"},
                "not even a dict",
            ]
        )
    )
    source = WebSearchNewsSource(llm=llm)

    items = source.headlines("SPY", datetime.now(UTC))

    assert len(items) == 1
    assert items[0].headline == "ok"


def test_entry_with_unparseable_published_at_is_skipped() -> None:
    llm = StubLLMClient(
        response=json.dumps([{"headline": "h", "summary": "s", "published_at": "not-a-date"}])
    )
    source = WebSearchNewsSource(llm=llm)

    assert source.headlines("SPY", datetime.now(UTC)) == []


def test_missing_url_defaults_to_none() -> None:
    llm = StubLLMClient(
        response=json.dumps(
            [{"headline": "h", "summary": "s", "published_at": "2026-07-10T00:00:00+00:00"}]
        )
    )
    source = WebSearchNewsSource(llm=llm)

    items = source.headlines("SPY", datetime.now(UTC))

    assert items[0].url is None
