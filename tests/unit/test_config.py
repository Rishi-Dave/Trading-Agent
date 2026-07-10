"""Config loader happy paths and whitelist semantics (SPEC §8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from etrade_agent.config import ConfigError, load_config


def test_valid_config_loads(valid_config_path: Path) -> None:
    config = load_config(valid_config_path)
    assert config.config_version == 1
    assert config.environment.mode == "sandbox"
    assert config.capital.pilot_amount_usd == 1000.0
    assert config.caps.per_trade_pct == 10.0
    assert config.caps.daily_trade_limit == 3
    assert config.caps.daily_loss_pct == 3.0
    assert config.policy.long_only is True
    assert config.policy.allowed_security_types == ["EQ"]


def test_enabled_symbols_unions_enabled_tiers_only(valid_config_path: Path) -> None:
    config = load_config(valid_config_path)
    assert config.whitelist.enabled_symbols() == frozenset({"SPY", "AAPL"})


def test_tier2_symbols_excluded_until_enabled(tmp_path: Path, valid_config_path: Path) -> None:
    text = valid_config_path.read_text().replace("tier2 = []", 'tier2 = ["GME"]')
    path = tmp_path / "tiered.toml"
    path.write_text(text)
    config = load_config(path)
    assert "GME" not in config.whitelist.enabled_symbols()

    both = tmp_path / "both.toml"
    both.write_text(text.replace('enabled_tiers = ["tier1"]', 'enabled_tiers = ["tier1", "tier2"]'))
    assert "GME" in load_config(both).whitelist.enabled_symbols()


def test_empty_enabled_tiers_rejected(tmp_path: Path, valid_config_path: Path) -> None:
    text = valid_config_path.read_text().replace('enabled_tiers = ["tier1"]', "enabled_tiers = []")
    path = tmp_path / "config.toml"
    path.write_text(text)
    with pytest.raises(ConfigError, match="enabled_tiers"):
        load_config(path)


def test_invalid_toml_refuses(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("this is not toml [")
    with pytest.raises(ConfigError, match="TOML"):
        load_config(path)
