"""In-memory preview→place binding (T2, ADR-0002).

A dict inside the running MCP server process — its lifetime IS "the same run"
(T2): a restart wipes it, so a place_order can never reference a preview from a
different run. Entries are one-shot (`consume`d after a successful place), so a
preview can't be replayed twice. The durable *decision receipt* is `trade_log`
(Phase 2, T4) — a separate layer; this store is purely the T2 authorization
binding, not an audit trail. `StoredPreview.reasoning_summary`/`signals_json`
(ADR-0004) are carried here only as the transport across the preview->place
boundary within one run — `server/tools.py` still does the actual
`trade_log` write via `store/state.py`; this dict is never itself read back
as a receipt.
"""

from __future__ import annotations

from dataclasses import dataclass

from etrade_agent.etrade.client import PreviewBinding
from etrade_agent.etrade.models import OrderPreview, OrderRequest

_NO_PIPELINE_REASONING = "no pipeline reasoning supplied with this call"


@dataclass(frozen=True)
class StoredPreview:
    order: OrderRequest
    preview: OrderPreview
    binding: PreviewBinding
    # T4/ADR-0004: reasoning is bound to the exact previewed order at preview
    # time, so a later place_order in this run inherits it with no new
    # place-time parameter — consistent with T2's "same run" binding. Default
    # is the honest receipt for a direct/manual preview_order call with no
    # pipeline behind it, not a claim that a pipeline ran.
    reasoning_summary: str = _NO_PIPELINE_REASONING
    signals_json: str = "[]"


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
