"""Remote kill-switch/breaker-reset trigger via ntfy.sh (ADR-0003 point 5,
SPEC §4.3 amendment). Tests target the pure per-message dispatch logic
(handle_message/dispatch) — the network stream loop (run/_stream_lines) is
exercised by hand against a real ntfy topic, not in this hermetic suite.

Authentication is a TOTP rotating code (RFC 6238), not a static reusable
token — a code review found the original static-token design was broadcast
in cleartext over the same ntfy topic it authenticated and became
permanently replayable after first use (ntfy has no per-subscriber
confidentiality and caches messages). A captured TOTP code expires in
~30-60s and can't be usefully replayed. Every valid action calls the
identical store/state.py writers the local CLIs use (no second enforcement
path) and is attributed changed_by="remote:ntfy" / breaker_reset_by="remote:ntfy".
"""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from etrade_agent.store import db
from etrade_agent.store.state import StateStore, today_utc
from etrade_agent.totp import generate_totp

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "remote_listener.py"
_SECRET = base64.b32encode(b"test-totp-shared-secret-value").decode()


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("remote_listener_script", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def remote_listener() -> ModuleType:
    return _load_module()


@pytest.fixture
def state(tmp_path: Path) -> StateStore:
    return StateStore(db.connect(tmp_path / "trading.db"))


def _current_code() -> str:
    return generate_totp(_SECRET)


def _event(title: str, message: str, event: str = "message") -> dict[str, object]:
    return {"event": event, "title": title, "message": message}


def test_non_message_events_are_ignored(remote_listener: ModuleType, state: StateStore) -> None:
    remote_listener.handle_message(
        _event("engage", _current_code(), event="open"),
        command_secret=_SECRET,
        state=state,
        notify_topic=None,
    )
    assert state.is_kill_engaged() is True  # unchanged from fresh-DB default


def test_unknown_title_is_ignored(remote_listener: ModuleType, state: StateStore) -> None:
    state.set_kill_switch(engaged=False, changed_by="test-setup")

    remote_listener.handle_message(
        _event("not-a-command", _current_code()),
        command_secret=_SECRET,
        state=state,
        notify_topic=None,
    )

    assert state.is_kill_engaged() is False  # unchanged


def test_wrong_code_is_rejected(remote_listener: ModuleType, state: StateStore) -> None:
    remote_listener.handle_message(
        _event("disengage", "000000"), command_secret=_SECRET, state=state, notify_topic=None
    )

    assert state.is_kill_engaged() is True  # unchanged: still the fresh-DB default


def test_code_from_a_different_secret_is_rejected(
    remote_listener: ModuleType, state: StateStore
) -> None:
    other_secret = base64.b32encode(b"a-completely-different-secret").decode()
    wrong_code = generate_totp(other_secret)

    remote_listener.handle_message(
        _event("disengage", wrong_code), command_secret=_SECRET, state=state, notify_topic=None
    )

    assert state.is_kill_engaged() is True


def test_engage_with_correct_code(remote_listener: ModuleType, state: StateStore) -> None:
    state.set_kill_switch(engaged=False, changed_by="test-setup")

    remote_listener.handle_message(
        _event("engage", _current_code()), command_secret=_SECRET, state=state, notify_topic=None
    )

    assert state.is_kill_engaged() is True


def test_disengage_with_correct_code(remote_listener: ModuleType, state: StateStore) -> None:
    remote_listener.handle_message(
        _event("disengage", _current_code()),
        command_secret=_SECRET,
        state=state,
        notify_topic=None,
    )

    assert state.is_kill_engaged() is False


def test_reset_breaker_with_correct_code(remote_listener: ModuleType, state: StateStore) -> None:
    state.trip_breaker(today_utc())

    remote_listener.handle_message(
        _event("reset-breaker", _current_code()),
        command_secret=_SECRET,
        state=state,
        notify_topic=None,
    )

    assert state.read_caps_state(today_utc()).breaker_tripped is False


def test_action_is_case_insensitive(remote_listener: ModuleType, state: StateStore) -> None:
    remote_listener.handle_message(
        _event("DISENGAGE", _current_code()),
        command_secret=_SECRET,
        state=state,
        notify_topic=None,
    )

    assert state.is_kill_engaged() is False


def test_records_remote_ntfy_as_operator(remote_listener: ModuleType, state: StateStore) -> None:
    remote_listener.handle_message(
        _event("disengage", _current_code()),
        command_secret=_SECRET,
        state=state,
        notify_topic=None,
    )

    row = state.conn.execute("SELECT changed_by FROM kill_switch WHERE id = 1").fetchone()
    assert row[0] == "remote:ntfy"


def test_sends_notification_on_successful_action(
    remote_listener: ModuleType, state: StateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        remote_listener.ntfy,
        "send",
        lambda topic, title, message, **k: sent.append((topic, title, message)),
    )

    remote_listener.handle_message(
        _event("disengage", _current_code()),
        command_secret=_SECRET,
        state=state,
        notify_topic="test-topic",
    )

    assert len(sent) == 1
    assert sent[0][0] == "test-topic"


def test_no_notification_sent_for_a_rejected_code(
    remote_listener: ModuleType, state: StateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*a: object, **k: object) -> None:
        raise AssertionError("ntfy.send must not be called for a rejected command")

    monkeypatch.setattr(remote_listener.ntfy, "send", _fail)

    remote_listener.handle_message(
        _event("disengage", "000000"),
        command_secret=_SECRET,
        state=state,
        notify_topic="test-topic",
    )


def test_run_refuses_outside_sandbox_mode(tmp_path: Path, remote_listener: ModuleType) -> None:
    from tests.conftest import VALID_CONFIG_TOML

    config_path = tmp_path / "config.toml"
    config_path.write_text(VALID_CONFIG_TOML.replace('mode = "sandbox"', 'mode = "prod"'))

    code = remote_listener.run(config_path, _SECRET, "irrelevant-topic", output=lambda _: None)

    assert code == 1
