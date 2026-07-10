"""MCP tool handlers (SPEC §5.2), implemented in Phase 1.

Every order-mutating handler calls the SafetyGate before any E*Trade call (T1);
place_order accepts only a preview_id (T2). Registration happens in app.py.
"""

from __future__ import annotations

from etrade_agent.etrade.client import EtradeClient
from etrade_agent.server.safety import SafetyGate


def register_tools(client: EtradeClient, gate: SafetyGate) -> None:
    """Register the six SPEC §5.2 tools on the FastMCP app (Phase 1)."""
    raise NotImplementedError("Phase 1 (SPEC §7)")
