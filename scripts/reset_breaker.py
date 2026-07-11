"""Manual daily-loss-breaker reset (SPEC §4.3, ADR-0003 point 4).

Reset is manual only and requires the operator: an interactive typed
confirmation (skippable via --yes for legitimate scripted/remote use — see
scripts/remote_listener.py) plus a mandatory --operator name, persisted into
caps_state.breaker_reset_by so the audit trail always names who acted. Logged
(logs.py) and notified (ntfy.sh; a missing NTFY_TOPIC degrades to a logged
warning, never blocks the reset). Sandbox-only, like every other Phase 2 code
path this phase (sandbox-prod skill).

Usage: uv run python scripts/reset_breaker.py --operator NAME [--yes] [--config PATH]
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

from etrade_agent import logs
from etrade_agent.config import ConfigError, load_config
from etrade_agent.notify import ntfy
from etrade_agent.store import db
from etrade_agent.store.state import StateStore, today_utc

_AGENT_ID = "reset-breaker"
DEFAULT_CONFIG_PATH = Path("config/config.toml")


def _resolve_db_path(config_path: Path, db_path_str: str) -> Path:
    db_path = Path(db_path_str)
    if db_path.is_absolute():
        return db_path
    return config_path.parent / db_path


def _run(
    config_path: Path,
    operator: str,
    *,
    skip_confirm: bool,
    prompt: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> int:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        output(f"refusing: {exc}")
        return 1

    if config.environment.mode != "sandbox":
        output("refusing: non-sandbox environment requires the sandbox-prod skill")
        return 1

    if not skip_confirm:
        response = prompt("Type 'reset' to confirm breaker reset: ")
        if response.strip() != "reset":
            output("confirmation not given; aborting")
            return 1

    state = StateStore(db.connect(_resolve_db_path(config_path, config.store.db_path)))
    day = today_utc()
    was_tripped = state.read_caps_state(day).breaker_tripped

    state.reset_breaker(day, reset_by=operator)

    logs.log(
        _AGENT_ID,
        "warning",
        "breaker manually reset",
        date_utc=day,
        operator=operator,
        was_tripped=was_tripped,
    )

    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        ntfy.send(topic, "Breaker reset", f"Loss breaker reset by {operator} for {day}.")
    else:
        logs.log(_AGENT_ID, "warning", "NTFY_TOPIC not set — reset notification skipped")

    output(f"breaker reset for {day} by {operator}")
    return 0


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Manually reset the daily loss breaker (SPEC §4.3)."
    )
    parser.add_argument(
        "--operator", required=True, help="Who is performing this reset (audit trail)."
    )
    parser.add_argument(
        "--yes", action="store_true", help="Skip the interactive typed confirmation."
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to config.toml."
    )
    args = parser.parse_args()
    return _run(args.config, args.operator, skip_confirm=args.yes)


if __name__ == "__main__":
    sys.exit(main())
