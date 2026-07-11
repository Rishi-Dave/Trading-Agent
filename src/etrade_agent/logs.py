"""Structured JSONL logging with secret redaction (SPEC §9, invariant T3).

Named `logs` (not `logging`) to avoid shadowing the stdlib module.
Every log line is one JSON object: {ts, level, agent_id, message, data}.
Values of known secret env vars are redacted before anything is written.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

_SECRET_ENV_VARS = (
    "ETRADE_CONSUMER_KEY",
    "ETRADE_CONSUMER_SECRET",
    "NTFY_TOPIC",
    "NTFY_COMMAND_SECRET",
)

_REDACTED = "[REDACTED]"


def _secret_values() -> list[str]:
    return [v for name in _SECRET_ENV_VARS if (v := os.environ.get(name))]


def redact(text: str) -> str:
    """Replace any known secret value appearing in text (T3)."""
    for value in _secret_values():
        text = text.replace(value, _REDACTED)
    return text


def log(
    agent_id: str,
    level: str,
    message: str,
    log_dir: Path | None = None,
    **data: Any,
) -> dict[str, Any]:
    """Emit one redacted JSONL line to stdout (stderr for errors) and optionally a file.

    Returns the emitted record (post-redaction), which tests assert against.
    """
    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "level": level,
        "agent_id": agent_id,
        "message": message,
    }
    if data:
        record["data"] = data

    line = redact(json.dumps(record, default=str))
    stream = sys.stderr if level == "error" else sys.stdout
    print(line, file=stream)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        with (log_dir / f"{agent_id}-{date}.jsonl").open("a") as fh:
            fh.write(line + "\n")

    return cast(dict[str, Any], json.loads(line))
