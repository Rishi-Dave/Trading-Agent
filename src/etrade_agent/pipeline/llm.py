"""LLM invocation seam (SPEC §6/§9, ADR-0004 point 2).

`pipeline/` cannot import `runner/` (module map, SPEC §3.1), and Claude Code's
WebSearch tool only exists inside a `claude -p`/`claude --print` invocation
(SPEC §9) — so no pipeline step shells out directly. Steps depend on this
Protocol structurally; the concrete claude-CLI-backed implementation
(`runner/llm_client.py::ClaudeLLMClient`) is wired in only by the Phase-4
runner, which owns `subprocess`/`claude -p` (nothing else imports `runner/`).
Tests and the pipeline wall inject a fake returning fixture text — no live
model call, no live network, in either.
"""

from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    def complete(self, prompt: str, *, allowed_tools: list[str] | None = None) -> str:
        """Single-shot completion. `allowed_tools` (e.g. `["WebSearch"]`) is a
        pass-through hint to the concrete adapter — this Protocol makes no
        claim about how (or whether) a given tool is actually available."""
        ...
