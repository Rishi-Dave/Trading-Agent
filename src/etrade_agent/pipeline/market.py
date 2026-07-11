"""Read-only market-data seam (ADR-0004 point 2).

Mirrors the shape of `server/safety.py::PositionsProvider` (ADR-0003 point 6)
but lives in `pipeline/` so analyst/risk steps never import `server/` (T1:
the pipeline proposes, the server disposes) and never reach `EtradeClient`'s
order-mutating methods (`preview_order`, `place_from_binding`) — only its
read-only market-data methods. `EtradeClient` satisfies this Protocol
structurally with no changes to `etrade/client.py`.
"""

from __future__ import annotations

from typing import Protocol

from etrade_agent.etrade.models import Balance, Position, Quote


class MarketDataSource(Protocol):
    def get_quote(self, symbol: str) -> Quote: ...
    def get_positions(self) -> list[Position]: ...
    def get_balances(self) -> Balance: ...
