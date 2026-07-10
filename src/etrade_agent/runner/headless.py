"""Headless Claude Code adapter (SPEC §9).

Drives `claude -p` (Max subscription, no API key). This module is the single seam
for the claude-CLI-vs-Agent-SDK decision: if the Agent SDK later supports Max
billing, only this file changes. Pattern reimplemented standalone (SPEC §3.2) —
no dependency on Agent-Creation's agent_factory.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentResult:
    stdout: str
    stderr: str
    returncode: int

    @property
    def success(self) -> bool:
        return self.returncode == 0


def is_claude_available() -> bool:
    return shutil.which("claude") is not None


def claude_query(
    prompt: str,
    system_prompt: str | None = None,
    max_turns: int = 1,
    timeout: int = 300,
) -> str:
    """Single-shot query. Returns stdout; raises RuntimeError on nonzero exit."""
    cmd = ["claude", "--print", "--max-turns", str(max_turns)]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout


def run_agent(
    prompt: str,
    allowed_tools: list[str],
    cwd: Path,
    timeout: int = 1800,
    max_turns: int | None = None,
    system_prompt: str | None = None,
    log_path: Path | None = None,
    on_output: Callable[[str], None] | None = None,
) -> AgentResult:
    """Multi-turn headless run with a tool whitelist, streaming tee, and hard timeout.

    The decision-run entrypoint (Phase 4): the pipeline session gets exactly the MCP
    tools it needs via allowed_tools — enforcement still lives in the server (T1).
    """
    cmd = ["claude", "--print"]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    if max_turns is not None:
        cmd += ["--max-turns", str(max_turns)]

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        stderr = f"[killed after {timeout}s timeout]\n{stderr}"

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(stdout)
    if on_output is not None:
        on_output(stdout)

    return AgentResult(stdout=stdout, stderr=stderr, returncode=proc.returncode)
