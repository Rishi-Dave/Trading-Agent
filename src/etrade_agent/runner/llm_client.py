"""Concrete `pipeline.llm.LLMClient` backed by headless `claude -p` (SPEC §9,
ADR-0004 point 2).

`runner/` is the only module allowed to shell out to the `claude` CLI
(`headless.py`); `pipeline/` depends only on the `LLMClient` Protocol, never
on this module (module map, SPEC §3.1 — nothing imports `runner/`). A
Phase-4 decision-run loop constructs `ClaudeLLMClient` and injects it into
pipeline steps; this file is otherwise dead code until that loop exists,
which is expected for this phase (ADR-0004).
"""

from __future__ import annotations

from dataclasses import dataclass

from etrade_agent.runner.headless import claude_query


@dataclass
class ClaudeLLMClient:
    """Wraps the existing single-shot `headless.py::claude_query`. `allowed_tools`
    (e.g. `["WebSearch"]`) maps to `claude --print --allowedTools ...`; the base
    single-shot query has no tool access unless explicitly requested."""

    max_turns: int = 1
    timeout: int = 300
    system_prompt: str | None = None

    def complete(self, prompt: str, *, allowed_tools: list[str] | None = None) -> str:
        return claude_query(
            prompt,
            system_prompt=self.system_prompt,
            max_turns=self.max_turns,
            timeout=self.timeout,
            allowed_tools=allowed_tools,
        )
