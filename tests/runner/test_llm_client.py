"""ClaudeLLMClient (ADR-0004 point 2) — the concrete pipeline.llm.LLMClient
backed by headless.py::claude_query. No live claude CLI call: claude_query
itself is monkeypatched (headless.py's own subprocess-level test coverage
lives in tests/runner/test_headless.py)."""

from __future__ import annotations

from typing import Any

import pytest

from etrade_agent.runner import llm_client


def test_complete_delegates_to_claude_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_claude_query(
        prompt: str,
        system_prompt: str | None = None,
        max_turns: int = 1,
        timeout: int = 300,
        allowed_tools: list[str] | None = None,
    ) -> str:
        captured.update(
            prompt=prompt,
            system_prompt=system_prompt,
            max_turns=max_turns,
            timeout=timeout,
            allowed_tools=allowed_tools,
        )
        return "the response"

    monkeypatch.setattr(llm_client, "claude_query", fake_claude_query)
    client = llm_client.ClaudeLLMClient()

    result = client.complete("hello", allowed_tools=["WebSearch"])

    assert result == "the response"
    assert captured["prompt"] == "hello"
    assert captured["allowed_tools"] == ["WebSearch"]
    assert captured["max_turns"] == 1
    assert captured["timeout"] == 300


def test_complete_passes_none_allowed_tools_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        llm_client,
        "claude_query",
        lambda *a, **k: captured.update(k) or "r",
    )
    client = llm_client.ClaudeLLMClient()

    client.complete("hello")

    assert captured["allowed_tools"] is None


def test_custom_max_turns_timeout_system_prompt_are_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        llm_client,
        "claude_query",
        lambda *a, **k: captured.update(k) or "r",
    )
    client = llm_client.ClaudeLLMClient(max_turns=3, timeout=60, system_prompt="be terse")

    client.complete("hello")

    assert captured["max_turns"] == 3
    assert captured["timeout"] == 60
    assert captured["system_prompt"] == "be terse"
