"""Shared test fixtures.

Note: test configs inject caps explicitly — there is no "default test caps" helper
by design (T5). If writing a test feels repetitive because of it, that friction is
the invariant working.
"""

from __future__ import annotations

from pathlib import Path

import pytest

VALID_CONFIG_TOML = """
config_version = 1

[environment]
mode = "sandbox"

[capital]
pilot_amount_usd = 1000.0

[caps]
per_trade_pct = 10.0
daily_trade_limit = 3
daily_loss_pct = 3.0

[whitelist]
tier1 = ["SPY", "AAPL"]
tier2 = []
tier3 = []
enabled_tiers = ["tier1"]

[policy]
long_only = true
allowed_security_types = ["EQ"]

[store]
db_path = "trading.db"
"""


@pytest.fixture
def valid_config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(VALID_CONFIG_TOML)
    return path
