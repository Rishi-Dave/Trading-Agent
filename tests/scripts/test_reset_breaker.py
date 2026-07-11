"""Manual breaker-reset CLI (SPEC §4.3, ADR-0003 point 4): typed confirmation
+ mandatory --operator, logged + notified.

scripts/ isn't a package on pythonpath (ADR-0001 keeps pythonpath = ["src"]);
load the module by file path, same pattern as test_oauth_login.py.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from etrade_agent import logs
from etrade_agent.notify import ntfy
from etrade_agent.store import db
from etrade_agent.store.state import StateStore, today_utc
from tests.conftest import VALID_CONFIG_TOML

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "reset_breaker.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("reset_breaker_script", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def reset_breaker() -> ModuleType:
    return _load_module()


def _config_path(tmp_path: Path, mode: str = "sandbox") -> Path:
    text = VALID_CONFIG_TOML.replace('mode = "sandbox"', f'mode = "{mode}"')
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def _tripped_state(tmp_path: Path) -> StateStore:
    state = StateStore(db.connect(tmp_path / "trading.db"))
    state.trip_breaker(today_utc())
    return state


def test_confirmation_required_by_default_and_wrong_input_aborts(
    tmp_path: Path, reset_breaker: ModuleType
) -> None:
    config_path = _config_path(tmp_path)
    state = _tripped_state(tmp_path)

    code = reset_breaker._run(
        config_path, "rishi", skip_confirm=False, prompt=lambda _: "nope", output=lambda _: None
    )

    assert code == 1
    assert state.read_caps_state(today_utc()).breaker_tripped is True


def test_correct_typed_confirmation_resets_breaker(
    tmp_path: Path, reset_breaker: ModuleType
) -> None:
    config_path = _config_path(tmp_path)
    state = _tripped_state(tmp_path)

    code = reset_breaker._run(
        config_path, "rishi", skip_confirm=False, prompt=lambda _: "reset", output=lambda _: None
    )

    assert code == 0
    snapshot = state.read_caps_state(today_utc())
    assert snapshot.breaker_tripped is False
    assert snapshot.breaker_reset_by == "rishi"


def test_yes_flag_skips_prompt(tmp_path: Path, reset_breaker: ModuleType) -> None:
    config_path = _config_path(tmp_path)
    state = _tripped_state(tmp_path)

    def _unreachable(_: str) -> str:
        raise AssertionError("prompt must not be called when skip_confirm=True")

    code = reset_breaker._run(
        config_path, "rishi", skip_confirm=True, prompt=_unreachable, output=lambda _: None
    )

    assert code == 0
    assert state.read_caps_state(today_utc()).breaker_tripped is False


def test_logs_the_reset_action(
    tmp_path: Path, reset_breaker: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        reset_breaker.logs,
        "log",
        lambda *a, **k: calls.append({"args": a, "kwargs": k}) or {},
    )
    config_path = _config_path(tmp_path)
    _tripped_state(tmp_path)

    reset_breaker._run(config_path, "rishi", skip_confirm=True, output=lambda _: None)

    assert any(c["kwargs"].get("operator") == "rishi" for c in calls)


def test_notifies_when_topic_set(
    tmp_path: Path, reset_breaker: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        reset_breaker.ntfy,
        "send",
        lambda topic, title, message, **k: sent.append((topic, title, message)),
    )
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    config_path = _config_path(tmp_path)
    _tripped_state(tmp_path)

    reset_breaker._run(config_path, "rishi", skip_confirm=True, output=lambda _: None)

    assert len(sent) == 1
    assert sent[0][0] == "test-topic"


def test_skips_notification_with_warning_when_topic_unset(
    tmp_path: Path, reset_breaker: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NTFY_TOPIC", raising=False)

    def _fail(*a: object, **k: object) -> None:
        raise AssertionError("ntfy.send must not be called with no topic configured")

    monkeypatch.setattr(reset_breaker.ntfy, "send", _fail)
    config_path = _config_path(tmp_path)
    _tripped_state(tmp_path)
    outputs: list[str] = []

    code = reset_breaker._run(config_path, "rishi", skip_confirm=True, output=outputs.append)

    assert code == 0  # a missing NTFY_TOPIC degrades gracefully, doesn't block the reset


def test_refuses_outside_sandbox_mode(tmp_path: Path, reset_breaker: ModuleType) -> None:
    config_path = _config_path(tmp_path, mode="prod")
    state = _tripped_state(tmp_path)

    code = reset_breaker._run(config_path, "rishi", skip_confirm=True, output=lambda _: None)

    assert code == 1
    assert state.read_caps_state(today_utc()).breaker_tripped is True


def test_missing_config_refuses(tmp_path: Path, reset_breaker: ModuleType) -> None:
    code = reset_breaker._run(
        tmp_path / "nonexistent.toml", "rishi", skip_confirm=True, output=lambda _: None
    )

    assert code == 1


# Sanity: the real logs/ntfy modules are indeed the ones imported (no accidental
# shadowing) — proves the monkeypatch targets above patch the right object.
def test_module_imports_real_logs_and_ntfy(reset_breaker: ModuleType) -> None:
    assert reset_breaker.logs is logs
    assert reset_breaker.ntfy is ntfy
