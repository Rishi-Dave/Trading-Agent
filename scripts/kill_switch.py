"""Manual kill-switch engage/disengage (SPEC §4.3, ADR-0003 point 4).

Engage/disengage is manual only and requires the operator: an interactive
typed confirmation (skippable via --yes for legitimate scripted/remote use —
see scripts/remote_listener.py) plus a mandatory --operator name, persisted
into kill_switch.changed_by. Logged (logs.py) and notified (ntfy.sh; a
missing NTFY_TOPIC degrades to a logged warning, never blocks the action).
Sandbox-only, like every other Phase 2 code path this phase (sandbox-prod
skill). Fresh databases ship with the switch engaged (SPEC §4.3).

Usage: uv run python scripts/kill_switch.py {engage,disengage} --operator NAME
       [--yes] [--config PATH]
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
from etrade_agent.store.state import StateStore

_AGENT_ID = "kill-switch"
_VALID_ACTIONS = ("engage", "disengage")
DEFAULT_CONFIG_PATH = Path("config/config.toml")


def _resolve_db_path(config_path: Path, db_path_str: str) -> Path:
    db_path = Path(db_path_str)
    if db_path.is_absolute():
        return db_path
    return config_path.parent / db_path


def _run(
    config_path: Path,
    action: str,
    operator: str,
    *,
    skip_confirm: bool,
    prompt: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> int:
    if action not in _VALID_ACTIONS:
        output(f"unknown action {action!r}; expected one of {_VALID_ACTIONS}")
        return 1

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        output(f"refusing: {exc}")
        return 1

    if config.environment.mode != "sandbox":
        output("refusing: non-sandbox environment requires the sandbox-prod skill")
        return 1

    if not skip_confirm:
        response = prompt(f"Type '{action}' to confirm kill switch {action}: ")
        if response.strip() != action:
            output("confirmation not given; aborting")
            return 1

    state = StateStore(db.connect(_resolve_db_path(config_path, config.store.db_path)))
    engaged = action == "engage"
    state.set_kill_switch(engaged=engaged, changed_by=operator, note=f"manual {action} via CLI")

    logs.log(_AGENT_ID, "warning", f"kill switch manually {action}d", operator=operator)

    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        ntfy.send(topic, "Kill switch changed", f"Kill switch {action}d by {operator}.")
    else:
        logs.log(_AGENT_ID, "warning", "NTFY_TOPIC not set — notification skipped")

    output(f"kill switch {action}d by {operator}")
    return 0


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Manually engage/disengage the kill switch (SPEC §4.3)."
    )
    parser.add_argument("action", choices=_VALID_ACTIONS)
    parser.add_argument(
        "--operator", required=True, help="Who is performing this action (audit trail)."
    )
    parser.add_argument(
        "--yes", action="store_true", help="Skip the interactive typed confirmation."
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to config.toml."
    )
    args = parser.parse_args()
    return _run(args.config, args.action, args.operator, skip_confirm=args.yes)


if __name__ == "__main__":
    sys.exit(main())
