"""Tests for the `python -m etrade_agent.runner` entrypoint (SPEC §9, the
plist's ProgramArguments target).

`main()` accepts injectable llm/news/notify so its own responsibility — wiring
build_runtime + run_decision, classifying startup failures, and never letting
an unexpected exception become a raw traceback in launchd's stderr log
(ADR-0002 point 9's carried-forward concern) — is testable without a live
`claude` process or live E*Trade network calls. The actual pipeline execution
these seams would drive is covered by tests/runner/test_decision_run.py and
the run wall (tests/wall/phase4/); these tests are entrypoint-wiring-focused.

Every `main()` call below passes an explicit `status_dir=tmp_path / "status"`
(Phase 5, SPEC §9, ADR-0006 Step 0 #2) — every exit path now writes a
best-effort status report, and without an explicit tmp-scoped status_dir the
default (`Path("status")`, relative to cwd) would write into the real repo
tree during a test run.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from etrade_agent.etrade import oauth
from etrade_agent.pipeline.news import NewsItem
from etrade_agent.runner.__main__ import main
from tests.conftest import VALID_CONFIG_TOML


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(VALID_CONFIG_TOML)
    return path


def _save_fake_tokens(tokens_dir: Path) -> None:
    oauth.save_tokens(oauth.OAuthTokens("faketoken", "fakesecret"), tokens_dir)


class _NotifyCollector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, title: str, message: str) -> None:
        self.calls.append((title, message))


class _FakeLLM:
    def complete(self, prompt: str, *, allowed_tools: list[str] | None = None) -> str:
        return '{"summary": "unused", "detail": {}}'


class _FakeNews:
    def headlines(self, symbol: str, since: datetime) -> list[NewsItem]:
        return []


def _hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Same hermeticity discipline as tests/server/test_app.py: no live
    # renewal call, fake consumer creds, explicit account id (skips the
    # /v1/accounts/list auto-resolve network call at EtradeClient.connect).
    monkeypatch.setattr("etrade_agent.server.app.oauth.renew_tokens", lambda tokens: tokens)
    monkeypatch.setenv("ETRADE_CONSUMER_KEY", "fakekey")
    monkeypatch.setenv("ETRADE_CONSUMER_SECRET", "fakesecret")
    monkeypatch.setenv("ETRADE_ACCOUNT_ID_KEY", "fake-account-key")
    monkeypatch.delenv("NTFY_TOPIC", raising=False)


def _read_only_status_report(status_dir: Path) -> dict[str, object]:
    files = list(status_dir.glob("*.json"))
    assert len(files) == 1
    result: dict[str, object] = json.loads(files[0].read_text())
    return result


def test_main_fails_closed_when_claude_unavailable_and_no_llm_injected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("etrade_agent.runner.__main__.is_claude_available", lambda: False)
    notify = _NotifyCollector()

    exit_code = main(
        tmp_path / "config.toml",
        tmp_path / "tokens",
        notify=notify,
        status_dir=tmp_path / "status",
    )

    assert exit_code == 1
    assert any("FAILED" in title for title, _ in notify.calls)


def test_main_writes_a_status_report_on_claude_unavailable_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("etrade_agent.runner.__main__.is_claude_available", lambda: False)
    status_dir = tmp_path / "status"

    main(
        tmp_path / "config.toml",
        tmp_path / "tokens",
        notify=_NotifyCollector(),
        status_dir=status_dir,
    )

    report = _read_only_status_report(status_dir)
    assert report["stage"] == "claude-unavailable"
    assert report["errors"]
    assert report["decisions_considered"] == 0


def test_main_skips_claude_check_when_llm_injected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A missing `claude` binary must not block a test-injected LLM — the
    # availability check exists only to short-circuit before constructing a
    # REAL ClaudeLLMClient.
    monkeypatch.setattr("etrade_agent.runner.__main__.is_claude_available", lambda: False)
    _hermetic_env(monkeypatch)
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)
    notify = _NotifyCollector()

    # Fresh DB ships kill_switch ENGAGED by default (SPEC §4.3) — run_decision
    # returns None immediately, so no live E*Trade network call ever happens.
    exit_code = main(
        path,
        tokens_dir,
        llm=_FakeLLM(),
        news=_FakeNews(),
        notify=notify,
        status_dir=tmp_path / "status",
    )

    assert exit_code == 0


def test_main_returns_nonzero_on_missing_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hermetic_env(monkeypatch)
    path = tmp_path / "config.toml"
    path.write_text('config_version = 1\n[environment]\nmode = "sandbox"\n')  # no [caps]
    notify = _NotifyCollector()

    exit_code = main(
        path,
        tmp_path / "tokens",
        llm=_FakeLLM(),
        news=_FakeNews(),
        notify=notify,
        status_dir=tmp_path / "status",
    )

    assert exit_code == 1
    assert any("FAILED" in title for title, _ in notify.calls)


def test_main_writes_a_status_report_on_missing_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hermetic_env(monkeypatch)
    path = tmp_path / "config.toml"
    path.write_text('config_version = 1\n[environment]\nmode = "sandbox"\n')  # no [caps]
    status_dir = tmp_path / "status"

    main(
        path,
        tmp_path / "tokens",
        llm=_FakeLLM(),
        news=_FakeNews(),
        notify=_NotifyCollector(),
        status_dir=status_dir,
    )

    report = _read_only_status_report(status_dir)
    assert report["stage"] == "config-error"
    assert report["errors"][0]["type"] == "ConfigError"


def test_main_flags_oauth_login_on_missing_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hermetic_env(monkeypatch)
    path = _write_config(tmp_path)
    notify = _NotifyCollector()

    exit_code = main(
        path,
        tmp_path / "no-tokens-here",
        llm=_FakeLLM(),
        news=_FakeNews(),
        notify=notify,
        status_dir=tmp_path / "status",
    )

    assert exit_code == 1
    assert any("oauth_login" in title or "oauth_login" in msg for title, msg in notify.calls)


def test_main_writes_a_status_report_on_oauth_login_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hermetic_env(monkeypatch)
    path = _write_config(tmp_path)
    status_dir = tmp_path / "status"

    main(
        path,
        tmp_path / "no-tokens-here",
        llm=_FakeLLM(),
        news=_FakeNews(),
        notify=_NotifyCollector(),
        status_dir=status_dir,
    )

    report = _read_only_status_report(status_dir)
    assert report["stage"] == "startup-error"
    assert report["errors"][0]["type"] == "ServerStartupError"


def test_main_wires_runtime_and_returns_zero_when_kill_switch_engaged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hermetic_env(monkeypatch)
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)
    notify = _NotifyCollector()

    # Fresh DB -> kill_switch engaged by default -> run_decision returns None
    # without ever touching the (fake-creds, would-be-live) EtradeClient.
    exit_code = main(
        path,
        tokens_dir,
        llm=_FakeLLM(),
        news=_FakeNews(),
        notify=notify,
        status_dir=tmp_path / "status",
    )

    assert exit_code == 0
    assert any("skip" in title.lower() for title, _msg in notify.calls)


def test_main_writes_a_status_report_when_kill_switch_engaged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hermetic_env(monkeypatch)
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)
    status_dir = tmp_path / "status"

    main(
        path,
        tokens_dir,
        llm=_FakeLLM(),
        news=_FakeNews(),
        notify=_NotifyCollector(),
        status_dir=status_dir,
    )

    report = _read_only_status_report(status_dir)
    assert report["stage"] == "skipped-kill-switch"


def test_main_returns_nonzero_when_run_decision_raises_unexpectedly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hermetic_env(monkeypatch)
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)
    notify = _NotifyCollector()

    def _raising_run_decision(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated pipeline crash")

    monkeypatch.setattr("etrade_agent.runner.__main__.run_decision", _raising_run_decision)

    exit_code = main(
        path,
        tokens_dir,
        llm=_FakeLLM(),
        news=_FakeNews(),
        notify=notify,
        status_dir=tmp_path / "status",
    )

    assert exit_code == 1
    assert any("FAILED" in title for title, _ in notify.calls)


def test_main_writes_a_status_report_on_an_unexpected_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hermetic_env(monkeypatch)
    path = _write_config(tmp_path)
    tokens_dir = tmp_path / "tokens"
    _save_fake_tokens(tokens_dir)
    status_dir = tmp_path / "status"

    def _raising_run_decision(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated pipeline crash")

    monkeypatch.setattr("etrade_agent.runner.__main__.run_decision", _raising_run_decision)

    main(
        path,
        tokens_dir,
        llm=_FakeLLM(),
        news=_FakeNews(),
        notify=_NotifyCollector(),
        status_dir=status_dir,
    )

    report = _read_only_status_report(status_dir)
    assert report["stage"] == "unexpected-exception"
    assert report["errors"][0]["type"] == "RuntimeError"
    assert "simulated pipeline crash" in report["errors"][0]["message"]


def test_main_run_id_is_stable_across_a_startup_failure_and_its_status_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run_id minted before build_runtime is attempted must be the same
    id the status report is filed under — proven by asserting there's
    exactly one report file and it's non-empty; a mismatch would either
    produce zero files (wrong id used to write) or the wrong id embedded."""
    _hermetic_env(monkeypatch)
    path = _write_config(tmp_path)
    status_dir = tmp_path / "status"

    main(
        path,
        tmp_path / "no-tokens-here",
        llm=_FakeLLM(),
        news=_FakeNews(),
        notify=_NotifyCollector(),
        status_dir=status_dir,
    )

    report = _read_only_status_report(status_dir)
    files = list(status_dir.glob("*.json"))
    assert files[0].stem == report["run_id"]
