"""Typed E*Trade REST client (SPEC §5.2). Sandbox first; base URL from environment mode."""

from __future__ import annotations

from etrade_agent.etrade.models import (
    Balance,
    OrderPreview,
    OrderRequest,
    OrderStatus,
    Position,
    Quote,
)

SANDBOX_BASE_URL = "https://apisb.etrade.com"
PROD_BASE_URL = "https://api.etrade.com"  # touched only via the sandbox-prod skill


class EtradeClient:
    """One method per SPEC §5.2 endpoint. Implemented against sandbox in Phase 1."""

    def get_quote(self, symbol: str) -> Quote:
        raise NotImplementedError("Phase 1 (SPEC §7)")

    def get_positions(self) -> list[Position]:
        raise NotImplementedError("Phase 1 (SPEC §7)")

    def get_balances(self) -> Balance:
        raise NotImplementedError("Phase 1 (SPEC §7)")

    def preview_order(self, order: OrderRequest) -> OrderPreview:
        raise NotImplementedError("Phase 1 (SPEC §7)")

    def place_order(self, preview_id: str) -> OrderStatus:
        """The ONLY path to the E*Trade order endpoint (T2): preview_id, never a raw order."""
        raise NotImplementedError("Phase 1 (SPEC §7)")

    def get_order_status(self, etrade_order_id: str) -> OrderStatus:
        raise NotImplementedError("Phase 1 (SPEC §7)")
