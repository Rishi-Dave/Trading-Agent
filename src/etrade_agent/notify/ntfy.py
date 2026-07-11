"""ntfy.sh notifications (SPEC §9) — one stdlib HTTP POST, no dependencies.

The topic comes from NTFY_TOPIC in .env: a long random string, never logged
(T3 redaction, logs.py), kept out of code/fixtures — basic hygiene against
casual discovery. It is NOT, on its own, an authorization boundary: ntfy
topics are a public pub/sub namespace with no per-subscriber confidentiality,
so anyone who does discover the topic can read everything published to it.
scripts/remote_listener.py (ADR-0003 point 5) relies on a separate TOTP
rotating code, not topic secrecy, to authorize kill-switch/breaker-reset
commands sent over this channel.
"""

from __future__ import annotations

import urllib.request
from collections.abc import Callable

from etrade_agent import logs

NTFY_BASE_URL = "https://ntfy.sh"

_AGENT_ID = "etrade-notify"

NotifyFn = Callable[[str, str], None]


def send(topic: str, title: str, message: str, priority: str = "default") -> None:
    """POST one notification. Raises on HTTP failure — callers decide whether a
    missed notification is fatal (breaker alerts: yes; daily digest: no)."""
    request = urllib.request.Request(
        f"{NTFY_BASE_URL}/{topic}",
        data=message.encode(),
        headers={"Title": title, "Priority": priority},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10):
        pass


def build_notify(topic: str | None) -> NotifyFn:
    """The production NotifyFn (SPEC §9): posts via `send` when NTFY_TOPIC is
    configured. A missing topic or a send failure must never abort a caller —
    by the time this is called the trade/refusal/digest already happened (or
    didn't); a missed notification is a monitoring gap, never a reason to fail
    (this module's own `send` docstring: callers decide whether a missed
    notification is fatal — here, it never is). Shared by both the runner
    (`runner/__main__.py`) and the safety gate (`server/app.py::build_runtime`,
    ADR-0006) — living here, not in `runner/`, so `server/` can build one
    without importing `runner/` (SPEC §3.1)."""

    def _notify(title: str, message: str) -> None:
        if not topic:
            logs.log(_AGENT_ID, "warning", "NTFY_TOPIC not set; skipping notification", title=title)
            return
        try:
            send(topic, title, message)
        except Exception as exc:  # a notification outage must not abort the caller
            logs.log(_AGENT_ID, "warning", "notification send failed", error=str(exc), title=title)

    return _notify
