"""Manual kill-switch CLI (SPEC §4.3, ADR-0003 point 4): engage/disengage,
typed confirmation + mandatory --operator, logged + notified."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from etrade_agent.store import db
from etrade_agent.store.state import StateStore
from tests.conftest import VALID_CONFIG_TOML

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "kill_switch.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("kill_switch_script", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def kill_switch() -> ModuleType:
    return _load_module()


def _config_path(tmp_path: Path, mode: str = "sandbox") -> Path:
    text = VALID_CONFIG_TOML.replace('mode = "sandbox"', f'mode = "{mode}"')
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def _state(tmp_path: Path) -> StateStore:
    return StateStore(db.connect(tmp_path / "trading.db"))


def test_fresh_db_ships_engaged(tmp_path: Path) -> None:
    assert _state(tmp_path).is_kill_engaged() is True


def test_disengage_requires_confirmation_and_wrong_input_aborts(
    tmp_path: Path, kill_switch: ModuleType
) -> None:
    config_path = _config_path(tmp_path)
    state = _state(tmp_path)

    code = kill_switch._run(
        config_path,
        "disengage",
        "rishi",
        skip_confirm=False,
        prompt=lambda _: "nope",
        output=lambda _: None,
    )

    assert code == 1
    assert state.is_kill_engaged() is True


def test_disengage_with_correct_confirmation(tmp_path: Path, kill_switch: ModuleType) -> None:
    config_path = _config_path(tmp_path)
    state = _state(tmp_path)

    code = kill_switch._run(
        config_path,
        "disengage",
        "rishi",
        skip_confirm=False,
        prompt=lambda _: "disengage",
        output=lambda _: None,
    )

    assert code == 0
    assert state.is_kill_engaged() is False


def test_engage_with_yes_flag_skips_prompt(tmp_path: Path, kill_switch: ModuleType) -> None:
    config_path = _config_path(tmp_path)
    state = _state(tmp_path)
    state.set_kill_switch(engaged=False, changed_by="test-setup")

    def _unreachable(_: str) -> str:
        raise AssertionError("prompt must not be called when skip_confirm=True")

    code = kill_switch._run(
        config_path,
        "engage",
        "rishi",
        skip_confirm=True,
        prompt=_unreachable,
        output=lambda _: None,
    )

    assert code == 0
    assert state.is_kill_engaged() is True


def test_operator_recorded_in_changed_by(tmp_path: Path, kill_switch: ModuleType) -> None:
    config_path = _config_path(tmp_path)
    state = _state(tmp_path)

    kill_switch._run(config_path, "disengage", "rishi", skip_confirm=True, output=lambda _: None)

    row = state.conn.execute("SELECT changed_by FROM kill_switch WHERE id = 1").fetchone()
    assert row[0] == "rishi"


def test_invalid_action_refuses(tmp_path: Path, kill_switch: ModuleType) -> None:
    config_path = _config_path(tmp_path)

    code = kill_switch._run(
        config_path, "sideways", "rishi", skip_confirm=True, output=lambda _: None
    )

    assert code == 1


def test_notifies_on_success(
    tmp_path: Path, kill_switch: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        kill_switch.ntfy,
        "send",
        lambda topic, title, message, **k: sent.append((topic, title, message)),
    )
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    config_path = _config_path(tmp_path)

    kill_switch._run(config_path, "engage", "rishi", skip_confirm=True, output=lambda _: None)

    assert len(sent) == 1


def test_refuses_outside_sandbox_mode(tmp_path: Path, kill_switch: ModuleType) -> None:
    config_path = _config_path(tmp_path, mode="prod")
    state = _state(tmp_path)

    code = kill_switch._run(
        config_path, "disengage", "rishi", skip_confirm=True, output=lambda _: None
    )

    assert code == 1
    assert state.is_kill_engaged() is True
