"""Caps wall — gate `caps-required` (SPEC §4.2, invariant T5).

Blocking in CI from day one. Every test here asserts the system REFUSES to run
without explicit caps. Do not weaken (safety-wall skill).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from etrade_agent.config import ConfigError, load_config
from etrade_agent.server.app import create_app
from tests.conftest import VALID_CONFIG_TOML

REPO_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_NO_DEFAULT_LINES = (
    "pilot_amount_usd",
    "per_trade_pct",
    "daily_trade_limit",
    "daily_loss_pct",
)


@pytest.mark.parametrize("missing_field", REQUIRED_NO_DEFAULT_LINES)
def test_missing_cap_refuses_to_load(tmp_path: Path, missing_field: str) -> None:
    """Omitting any single cap (or the pilot amount) must raise ConfigError naming it."""
    lines = [line for line in VALID_CONFIG_TOML.splitlines() if not line.startswith(missing_field)]
    path = tmp_path / "config.toml"
    path.write_text("\n".join(lines))

    with pytest.raises(ConfigError, match=missing_field):
        load_config(path)


def test_example_config_as_shipped_refuses_to_load() -> None:
    """config.example.toml must NOT load as-is — proof there are no hidden cap defaults."""
    example = REPO_ROOT / "config" / "config.example.toml"
    assert example.exists(), "config/config.example.toml missing from repo"
    with pytest.raises(ConfigError):
        load_config(example)


def test_missing_config_file_refuses(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nonexistent.toml")


def test_server_factory_dies_without_caps(tmp_path: Path) -> None:
    """create_app must raise ConfigError (not NotImplementedError, not a default)
    when caps are absent — the startup gate runs before anything else exists."""
    path = tmp_path / "config.toml"
    path.write_text('config_version = 1\n[environment]\nmode = "sandbox"\n')

    with pytest.raises(ConfigError):
        create_app(path)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("per_trade_pct", "0"),
        ("per_trade_pct", "101"),
        ("per_trade_pct", "-5"),
        ("daily_trade_limit", "0"),
        ("daily_trade_limit", "-1"),
        ("daily_loss_pct", "0"),
        ("daily_loss_pct", "150"),
        ("pilot_amount_usd", "0"),
        ("pilot_amount_usd", "-100"),
    ],
)
def test_out_of_range_cap_refuses(tmp_path: Path, field: str, bad_value: str) -> None:
    """Invalid cap values are refused, never clamped or warned past (T5)."""
    lines = [
        f"{field} = {bad_value}" if line.startswith(field) else line
        for line in VALID_CONFIG_TOML.splitlines()
    ]
    path = tmp_path / "config.toml"
    path.write_text("\n".join(lines))

    with pytest.raises(ConfigError, match=field):
        load_config(path)
