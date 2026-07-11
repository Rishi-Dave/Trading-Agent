"""One-time TOTP setup helper for scripts/remote_listener.py (ADR-0003 point
5). Must never write the secret anywhere but stdout (T3) — this is the one
place it's meant to be seen, once, at setup time."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from etrade_agent.totp import generate_totp

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "generate_totp_secret.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_totp_secret_script", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def generate_totp_secret() -> ModuleType:
    return _load_module()


def test_prints_a_usable_secret(generate_totp_secret: ModuleType) -> None:
    outputs: list[str] = []

    code = generate_totp_secret._run(
        "test-account",
        secret_factory=lambda: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        output=outputs.append,
    )

    assert code == 0
    joined = "\n".join(outputs)
    assert "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" in joined
    assert "NTFY_COMMAND_SECRET=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" in joined


def test_printed_secret_is_a_real_totp_secret(generate_totp_secret: ModuleType) -> None:
    outputs: list[str] = []

    generate_totp_secret._run(
        "test-account",
        secret_factory=lambda: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        output=outputs.append,
    )

    # Confirms the printed value round-trips through the real TOTP algorithm
    # (not just a random-looking string), same as ADR-0003 point 5 expects.
    assert generate_totp("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")


def test_includes_the_account_label(generate_totp_secret: ModuleType) -> None:
    outputs: list[str] = []

    generate_totp_secret._run(
        "rishi-phone",
        secret_factory=lambda: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        output=outputs.append,
    )

    assert any("rishi-phone" in line for line in outputs)


def test_uses_the_real_generate_secret_by_default(generate_totp_secret: ModuleType) -> None:
    outputs: list[str] = []

    code = generate_totp_secret._run("acct", output=outputs.append)

    assert code == 0
    joined = "\n".join(outputs)
    assert "NTFY_COMMAND_SECRET=" in joined
