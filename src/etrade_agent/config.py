"""Config loading and validation (SPEC §8).

Caps and pilot capital have NO defaults (invariant T5): a config file that omits
them raises ConfigError naming every missing/invalid field, and server startup
dies on it (gate `caps-required`, SPEC §4.2).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator


class ConfigError(Exception):
    """Config is missing or invalid. The message names every offending field."""


class EnvironmentConfig(BaseModel):
    mode: Literal["sandbox", "prod"] = "sandbox"


class CapitalConfig(BaseModel):
    pilot_amount_usd: float = Field(gt=0)


class Caps(BaseModel):
    """SPEC §8.1 [caps] — all required, no defaults (T5)."""

    per_trade_pct: float = Field(gt=0, le=100)
    daily_trade_limit: int = Field(gt=0)
    daily_loss_pct: float = Field(gt=0, le=100)


class Whitelist(BaseModel):
    tier1: list[str] = Field(default_factory=list)
    tier2: list[str] = Field(default_factory=list)
    tier3: list[str] = Field(default_factory=list)
    enabled_tiers: list[Literal["tier1", "tier2", "tier3"]] = Field(min_length=1)

    @model_validator(mode="after")
    def enabled_tiers_nonempty(self) -> Whitelist:
        symbols = self.enabled_symbols()
        if not symbols:
            raise ValueError("enabled_tiers select no symbols — whitelist would refuse everything")
        return self

    def enabled_symbols(self) -> frozenset[str]:
        return frozenset(s for tier in self.enabled_tiers for s in getattr(self, tier))


class Policy(BaseModel):
    long_only: bool = True
    allowed_security_types: list[str] = Field(default_factory=lambda: ["EQ"])


class StoreConfig(BaseModel):
    db_path: str = "trading.db"


class AppConfig(BaseModel):
    config_version: int = Field(ge=1)
    environment: EnvironmentConfig
    capital: CapitalConfig
    caps: Caps
    whitelist: Whitelist
    policy: Policy
    store: StoreConfig


def load_config(path: Path) -> AppConfig:
    """Load and validate config.toml. Raises ConfigError on any missing/invalid field."""
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config is not valid TOML: {exc}") from exc
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"invalid config ({path}): {problems}") from exc
