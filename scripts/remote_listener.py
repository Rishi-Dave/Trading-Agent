"""Remote kill-switch/breaker-reset trigger via ntfy.sh (ADR-0003 point 5,
SPEC §4.3 amendment; TOTP revision post-review).

Subscribes to a private ntfy command topic and, for each message whose body
is a VALID CURRENT TOTP CODE (RFC 6238, ~30s rotation) for the shared secret,
dispatches the action named in the message title: "engage", "disengage", or
"reset-breaker". The topic name is NOT treated as the authorization
credential — ntfy topics are guessable by design. Earlier revisions of this
script authenticated with a static, reusable command token sent in the
message body; a code review found that token was broadcast in cleartext over
the same topic it authenticated (ntfy has no per-subscriber confidentiality
and caches messages), making it permanently visible and replayable after
first legitimate use. TOTP closes that hole: a captured code expires within
one rotation and can't be usefully replayed. The operator reads the current
code from a standard authenticator app set up once with NTFY_COMMAND_SECRET
(etrade_agent.totp.generate_secret()) — the code is never transmitted to
compute, only to prove.

Calls the identical scripts/kill_switch.py-equivalent store/state.py writers
used by the local CLIs (no second, divergent enforcement path) and attributes
every action to changed_by="remote:ntfy" / breaker_reset_by="remote:ntfy" so
the audit trail distinguishes remote from local actions. Every action
(accepted or rejected) is logged; accepted actions also send a confirmation
notification. Sandbox-only, same as scripts/kill_switch.py /
scripts/reset_breaker.py.

Usage: uv run python scripts/remote_listener.py [--config PATH]
Requires .env: NTFY_TOPIC, NTFY_COMMAND_SECRET (base32 TOTP secret — generate
with: uv run python -c "from etrade_agent.totp import generate_secret;
print(generate_secret())", then enter it into an authenticator app)
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from dotenv import load_dotenv

from etrade_agent import logs
from etrade_agent.config import ConfigError, load_config
from etrade_agent.notify import ntfy
from etrade_agent.store import db
from etrade_agent.store.state import StateStore, today_utc
from etrade_agent.totp import verify_totp

_AGENT_ID = "remote-listener"
_OPERATOR = "remote:ntfy"
_VALID_ACTIONS = ("engage", "disengage", "reset-breaker")
_NTFY_JSON_URL = "https://ntfy.sh/{topic}/json"
DEFAULT_CONFIG_PATH = Path("config/config.toml")


def _resolve_db_path(config_path: Path, db_path_str: str) -> Path:
    db_path = Path(db_path_str)
    if db_path.is_absolute():
        return db_path
    return config_path.parent / db_path


def dispatch(action: str, state: StateStore) -> None:
    """Apply one validated remote action via the same StateStore writers the
    local CLIs use (ADR-0003 point 5) — no second enforcement path."""
    if action == "engage":
        state.set_kill_switch(engaged=True, changed_by=_OPERATOR, note="remote engage via ntfy")
    elif action == "disengage":
        state.set_kill_switch(engaged=False, changed_by=_OPERATOR, note="remote disengage via ntfy")
    elif action == "reset-breaker":
        state.reset_breaker(today_utc(), reset_by=_OPERATOR)
    else:  # pragma: no cover - unreachable, callers pre-filter against _VALID_ACTIONS
        raise ValueError(f"unknown action: {action}")


def handle_message(
    event: dict[str, Any],
    *,
    command_secret: str,
    state: StateStore,
    notify_topic: str | None,
) -> None:
    """Validate and apply one ntfy stream event. Every accepted OR rejected
    command attempt is logged (T3-safe: logs.py redacts NTFY_COMMAND_SECRET;
    a rejected code is itself never logged verbatim)."""
    if event.get("event") != "message":
        return  # ntfy stream also emits "open"/"keepalive" events — not commands

    title = str(event.get("title") or "").strip().lower()
    code = str(event.get("message") or "").strip()

    if title not in _VALID_ACTIONS:
        return  # not a recognized command; ignore silently (could be a stray push)

    if not verify_totp(command_secret, code):
        logs.log(_AGENT_ID, "warning", "remote command rejected: bad or expired code", action=title)
        return

    dispatch(title, state)
    logs.log(_AGENT_ID, "warning", "remote safety action applied", action=title, operator=_OPERATOR)

    if notify_topic:
        ntfy.send(
            notify_topic,
            "Remote safety action applied",
            f"'{title}' triggered via ntfy ({_OPERATOR}).",
        )
    else:
        logs.log(_AGENT_ID, "warning", "NTFY_TOPIC not set — confirmation notification skipped")


def _stream_lines(topic: str) -> Iterator[str]:  # pragma: no cover - live network I/O
    url = _NTFY_JSON_URL.format(topic=topic)
    with urlopen(url) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if line:
                yield line


def run(
    config_path: Path,
    command_secret: str,
    topic: str,
    *,
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

    state = StateStore(db.connect(_resolve_db_path(config_path, config.store.db_path)))
    output(f"listening for remote commands on ntfy (sandbox={config.environment.mode})")

    for line in _stream_lines(topic):  # pragma: no cover - live network I/O
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        handle_message(event, command_secret=command_secret, state=state, notify_topic=topic)

    return 0


def main() -> int:
    load_dotenv()
    topic = os.environ.get("NTFY_TOPIC")
    command_secret = os.environ.get("NTFY_COMMAND_SECRET")
    if not topic or not command_secret:
        print("NTFY_TOPIC and NTFY_COMMAND_SECRET must both be set in .env", file=sys.stderr)
        return 1
    return run(DEFAULT_CONFIG_PATH, command_secret, topic)


if __name__ == "__main__":
    sys.exit(main())
