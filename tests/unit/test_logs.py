"""JSONL log shape and secret redaction (SPEC §9, invariant T3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from etrade_agent.logs import log, redact


def test_jsonl_record_shape(capsys: pytest.CaptureFixture[str]) -> None:
    record = log("test-agent", "info", "hello", detail=42)
    assert set(record) == {"ts", "level", "agent_id", "message", "data"}
    assert record["level"] == "info"
    assert record["agent_id"] == "test-agent"
    assert record["data"] == {"detail": 42}
    # stdout line is the same JSON object
    assert json.loads(capsys.readouterr().out.strip()) == record


def test_errors_go_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    log("test-agent", "error", "boom")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "boom" in captured.err


def test_secret_values_redacted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "sekrit-consumer-key-12345"
    monkeypatch.setenv("ETRADE_CONSUMER_KEY", secret)

    record = log("test-agent", "info", f"key is {secret}", token=secret)

    emitted = json.dumps(record)
    assert secret not in emitted
    assert "[REDACTED]" in record["message"]
    assert secret not in capsys.readouterr().out


def test_redact_handles_multiple_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ETRADE_CONSUMER_SECRET", "aaa-secret")
    monkeypatch.setenv("NTFY_TOPIC", "bbb-topic")
    assert redact("aaa-secret and bbb-topic") == "[REDACTED] and [REDACTED]"


def test_file_sink_writes_redacted_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "file-sink-secret"
    monkeypatch.setenv("NTFY_TOPIC", secret)
    log("file-agent", "info", f"topic {secret}", log_dir=tmp_path)

    files = list(tmp_path.glob("file-agent-*.jsonl"))
    assert len(files) == 1
    content = files[0].read_text()
    assert secret not in content
    assert json.loads(content.strip())["agent_id"] == "file-agent"
