"""headless.py::claude_query — single-shot claude -p adapter. No live claude
CLI call: subprocess.run is monkeypatched."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from etrade_agent.runner import headless


@dataclass
class _FakeCompletedProcess:
    returncode: int = 0
    stdout: str = "response text"
    stderr: str = ""


@dataclass
class _RecordingRun:
    calls: list[list[str]] = field(default_factory=list)
    result: _FakeCompletedProcess = field(default_factory=_FakeCompletedProcess)

    def __call__(self, cmd: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        self.calls.append(cmd)
        return self.result


def test_claude_query_omits_allowed_tools_flag_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _RecordingRun()
    monkeypatch.setattr(headless.subprocess, "run", recorder)

    result = headless.claude_query("prompt")

    assert result == "response text"
    assert "--allowedTools" not in recorder.calls[0]


def test_claude_query_passes_allowed_tools_as_comma_joined_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _RecordingRun()
    monkeypatch.setattr(headless.subprocess, "run", recorder)

    headless.claude_query("prompt", allowed_tools=["WebSearch", "Read"])

    cmd = recorder.calls[0]
    assert "--allowedTools" in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "WebSearch,Read"


def test_claude_query_raises_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _RecordingRun(result=_FakeCompletedProcess(returncode=1, stderr="boom"))
    monkeypatch.setattr(headless.subprocess, "run", recorder)

    with pytest.raises(RuntimeError, match="boom"):
        headless.claude_query("prompt")
