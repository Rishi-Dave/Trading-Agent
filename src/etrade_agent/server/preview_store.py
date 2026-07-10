"""In-memory preview→place binding (T2, ADR-0002).

A dict inside the running MCP server process — its lifetime IS "the same run"
(T2): a restart wipes it, so a place_order can never reference a preview from a
different run. Entries are one-shot (`consume`d after a successful place), so a
preview can't be replayed twice. The durable *decision receipt* is `trade_log`
(Phase 2, T4) — a separate layer; this store is purely the T2 authorization
binding, not an audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass

from etrade_agent.etrade.client import PreviewBinding
from etrade_agent.etrade.models import OrderPreview, OrderRequest


@dataclass(frozen=True)
class StoredPreview:
    order: OrderRequest
    preview: OrderPreview
    binding: PreviewBinding


class PreviewStore:
    def __init__(self) -> None:
        self._entries: dict[str, StoredPreview] = {}

    def put(self, entry: StoredPreview) -> None:
        self._entries[entry.preview.preview_id] = entry

    def get(self, preview_id: str) -> StoredPreview | None:
        return self._entries.get(preview_id)

    def consume(self, preview_id: str) -> None:
        """Remove the entry if present; a no-op if not (never raises)."""
        self._entries.pop(preview_id, None)
