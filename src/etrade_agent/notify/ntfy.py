"""ntfy.sh notifications (SPEC §9) — one stdlib HTTP POST, no dependencies.

The topic comes from NTFY_TOPIC in .env and is treated as a secret (T3): topics
are a public namespace, so it must be a long random string and never logged.
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
