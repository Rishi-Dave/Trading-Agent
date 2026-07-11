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

NTFY_BASE_URL = "https://ntfy.sh"


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
