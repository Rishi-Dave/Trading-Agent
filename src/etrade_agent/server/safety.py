"""Safety layer: the ONLY enforcement point for caps/whitelist/breaker/kill switch (T1).

Every gate in SPEC §4.2 becomes a check here in Phase 2; each maps 1:1 to a wall
test in tests/wall/. Gates evaluate the preview result (T2), fail closed, and
refuse with the exact SPEC §4.1 payload shape.

Gate evaluation order (ADR-0003 point 1) at `check_place`, when more than one
gate would refuse the same order: halts (kill-switch, loss-breaker,
daily-trade-limit) -> legality (whitelist, policy-security-type,
policy-long-only) -> sizing (capital-ceiling, per-trade-cap). The operator sees
the most operationally significant reason first.

`check_preview` / `check_priced_preview` / `check_place` split (ADR-0003 point
7): `OrderRequest` alone carries no `estimated_cost` (that only exists on
`OrderPreview`, produced by `EtradeClient.preview_order` — ADR-0002 point 5), so
the cost-dependent gates (capital-ceiling, per-trade-cap) run once, on the
authoritative priced figure, via `check_priced_preview` — called from
`server/tools.py::preview_order` immediately after pricing and before the
binding is stored. `check_place` re-runs the FULL gate set (state can change
between preview and place), kill-switch first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from etrade_agent import logs
from etrade_agent.config import AppConfig
from etrade_agent.etrade.models import (
    OrderAction,
    OrderPreview,
    OrderRequest,
    Position,
    unrealized_pnl,
)
from etrade_agent.notify.ntfy import NotifyFn
from etrade_agent.store.state import StateStore, today_utc

_AGENT_ID = "etrade-server"


@dataclass(frozen=True)
class Refusal:
    """SPEC §4.1 refusal payload. This shape is a parsed contract — do not drift it."""

    gate: str
    reason: str
    state: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {"refused": True, "gate": self.gate, "reason": self.reason, "state": self.state}


class PositionsProvider(Protocol):
    """Read-only market-data dependency for gates that need live positions
    (capital-ceiling, loss-breaker, policy-long-only). Deliberately narrower
    than EtradeClient (ADR-0003 point 6): a gate that could reach
    `preview_order`/`place_from_binding` would reopen exactly the gate-bypass
    surface T1 exists to prevent. `EtradeClient` satisfies this structurally —
    no changes needed there. No gate currently needs balances (cash/buying
    power) — this stays a one-method Protocol until one does (YAGNI,
    code-review finding); add `get_balances` back only alongside the gate
    that actually calls it."""

    def get_positions(self) -> list[Position]: ...


class SafetyGate(Protocol):
    """Checked by tool handlers BEFORE any E*Trade call (SPEC §4.2)."""

    def check_preview(self, order: OrderRequest) -> Refusal | None:
        """Order-only gates, decidable before any E*Trade call: whitelist,
        policy-security-type, policy-long-only."""
        ...

    def check_priced_preview(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        """Cost-dependent preview-time gates, run once pricing exists:
        capital-ceiling, per-trade-cap (ADR-0003 point 7)."""
        ...

    def check_place(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        """All gates, kill-switch first (SPEC §4.2). None means the order may proceed."""
        ...


class ConfiguredSafetyGate:
    """Phase 2 implementation (SPEC §7). Construction requires valid caps (T5)."""

    def __init__(
        self,
        config: AppConfig,
        market: PositionsProvider,
        state: StateStore,
        *,
        notify: NotifyFn | None = None,
    ) -> None:
        # AppConfig cannot be constructed without caps (T5); keeping the whole config
        # here means every gate reads the same validated snapshot.
        self._config = config
        self._market = market
        self._state = state
        # SPEC §3.1 amendment (ADR-0006): server/ may import notify so a
        # loss-breaker trip notifies at the source of truth, regardless of
        # caller (the runner's execute_decisions loop, or a manual
        # .mcp.json place_order). Optional/no-op by default so every existing
        # three-positional-arg construction keeps working unchanged.
        self._notify = notify if notify is not None else (lambda title, message: None)

    # -- SafetyGate protocol ---------------------------------------------

    def check_preview(self, order: OrderRequest) -> Refusal | None:
        try:
            refusal = self._check_whitelist(order)
            if refusal is not None:
                return refusal
            refusal = self._check_policy_security_type(order)
            if refusal is not None:
                return refusal
            # Fetched only if the order survives the two free (no-API-call)
            # checks above, and only once (code-review finding: this used to
            # be an independent get_positions() call per sub-check).
            positions = self._market.get_positions()
            return self._check_policy_long_only(order, positions)
        except Exception as exc:  # fail closed (server/CLAUDE.md)
            return self._fail_closed_refusal(exc)

    def check_priced_preview(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        try:
            positions = self._market.get_positions()
            refusal = self._check_capital_ceiling(preview, order, positions)
            if refusal is not None:
                return refusal
            return self._check_per_trade_cap(preview, order)
        except Exception as exc:  # fail closed (server/CLAUDE.md)
            return self._fail_closed_refusal(exc)

    def check_place(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        try:
            # Halts.
            refusal = self._check_kill_switch()
            if refusal is not None:
                return refusal
            # One positions fetch for the whole pass (code-review finding:
            # this used to be up to three independent live calls — besides
            # the redundant latency/rate-limit cost, it widened the window
            # between the kill-switch check and the eventual place call).
            positions = self._market.get_positions()
            refusal = self._check_loss_breaker(positions)
            if refusal is not None:
                return refusal
            refusal = self._check_daily_trade_limit()
            if refusal is not None:
                return refusal
            # Legality.
            refusal = self._check_whitelist(order)
            if refusal is not None:
                return refusal
            refusal = self._check_policy_security_type(order)
            if refusal is not None:
                return refusal
            refusal = self._check_policy_long_only(order, positions)
            if refusal is not None:
                return refusal
            # Sizing.
            refusal = self._check_capital_ceiling(preview, order, positions)
            if refusal is not None:
                return refusal
            return self._check_per_trade_cap(preview, order)
        except Exception as exc:  # fail closed (server/CLAUDE.md)
            return self._fail_closed_refusal(exc)

    # -- halts --------------------------------------------------------------

    def _check_kill_switch(self) -> Refusal | None:
        if self._state.is_kill_engaged():
            return Refusal(
                gate="kill-switch",
                reason="kill switch is engaged; all place_order calls are refused",
                state={"engaged": True},
            )
        return None

    def _check_loss_breaker(self, positions: list[Position]) -> Refusal | None:
        day = today_utc()
        snapshot = self._state.read_caps_state(day)
        if snapshot.breaker_tripped:
            return Refusal(
                gate="loss-breaker",
                reason=f"daily loss breaker is already tripped for {day}; manual reset required",
                state={"date_utc": day, "breaker_tripped_ts": snapshot.breaker_tripped_ts},
            )

        unrealized = self._unrealized_pnl(positions)
        total_pnl = snapshot.realized_pnl + unrealized
        threshold = self._loss_threshold_usd()
        if total_pnl <= threshold:
            self._state.trip_breaker(day)
            reason = (
                f"daily P&L {total_pnl:.2f} breached the "
                f"-{self._config.caps.daily_loss_pct}% threshold ({threshold:.2f}); "
                "breaker tripped"
            )
            # Fresh trip only (SPEC §9, ADR-0006 Step 0 #3) — the
            # already-tripped branch above never calls trip_breaker and never
            # notifies, so this fires at most once per UTC day regardless of
            # how many subsequent orders get refused.
            self._safe_notify("Breaker tripped", reason)
            return Refusal(
                gate="loss-breaker",
                reason=reason,
                state={
                    "date_utc": day,
                    "realized_pnl": snapshot.realized_pnl,
                    "unrealized_pnl": unrealized,
                    "total_pnl": total_pnl,
                    "threshold_usd": threshold,
                },
            )
        return None

    def _loss_threshold_usd(self) -> float:
        return -(self._config.caps.daily_loss_pct / 100.0) * self._config.capital.pilot_amount_usd

    def _unrealized_pnl(self, positions: list[Position]) -> float:
        # Live positions only (ADR-0003 point 3) — positions_cache is advisory
        # and never trusted for this safety calculation. Delegates to the
        # shared etrade.models.unrealized_pnl so the daily digest
        # (runner/decision_run.py, ADR-0006) reads the identical calculation,
        # never a second, potentially-drifting one.
        return unrealized_pnl(positions)

    def _safe_notify(self, title: str, message: str) -> None:
        # T1: a notify-channel outage must never mask the real gate result —
        # the breaker has already tripped in the DB by the time this runs, so
        # a raising NotifyFn must not propagate up and get converted into a
        # generic "internal-error" refusal by check_place's own try/except
        # (that would hide gate=loss-breaker behind an unrelated failure).
        try:
            self._notify(title, logs.redact(message))
        except Exception as exc:
            logs.log(_AGENT_ID, "warning", "breaker-tripped notification failed", error=str(exc))

    def _check_daily_trade_limit(self) -> Refusal | None:
        day = today_utc()
        snapshot = self._state.read_caps_state(day)
        if snapshot.trades_executed >= self._config.caps.daily_trade_limit:
            return Refusal(
                gate="daily-trade-limit",
                reason=(
                    f"{snapshot.trades_executed} trades already executed today "
                    f"(limit {self._config.caps.daily_trade_limit})"
                ),
                state={
                    "date_utc": day,
                    "trades_executed": snapshot.trades_executed,
                    "daily_trade_limit": self._config.caps.daily_trade_limit,
                },
            )
        return None

    # -- legality -------------------------------------------------------

    def _check_whitelist(self, order: OrderRequest) -> Refusal | None:
        enabled = self._config.whitelist.enabled_symbols()
        if order.symbol not in enabled:
            return Refusal(
                gate="whitelist",
                reason=f"{order.symbol} is not in an enabled whitelist tier",
                state={"symbol": order.symbol, "enabled_symbols": sorted(enabled)},
            )
        return None

    def _check_policy_security_type(self, order: OrderRequest) -> Refusal | None:
        allowed = self._config.policy.allowed_security_types
        if order.security_type not in allowed:
            return Refusal(
                gate="policy-security-type",
                reason=f"{order.security_type} is not an allowed security type (v1: {allowed})",
                state={
                    "security_type": order.security_type.value,
                    "allowed_security_types": list(allowed),
                },
            )
        return None

    def _check_policy_long_only(
        self, order: OrderRequest, positions: list[Position]
    ) -> Refusal | None:
        if not self._config.policy.long_only:
            return None
        if order.order_action is not OrderAction.SELL:
            return None
        held = self._held_quantity(order.symbol, positions)
        if order.quantity > held:
            return Refusal(
                gate="policy-long-only",
                reason=(
                    f"SELL {order.quantity} {order.symbol} exceeds held quantity {held} "
                    "(no shorting, T6)"
                ),
                state={
                    "symbol": order.symbol,
                    "requested_quantity": order.quantity,
                    "held_quantity": held,
                },
            )
        return None

    def _held_quantity(self, symbol: str, positions: list[Position]) -> float:
        return next((p.quantity for p in positions if p.symbol == symbol), 0.0)

    # -- sizing -----------------------------------------------------------

    def _check_capital_ceiling(
        self, preview: OrderPreview, order: OrderRequest, positions: list[Position]
    ) -> Refusal | None:
        if order.order_action is not OrderAction.BUY:
            # A SELL reduces exposure — it never adds new capital at risk. The
            # position being sold is already counted in current_exposure, so
            # adding estimated_cost on top would double-count it and could
            # block a legitimate exit (code-review finding). policy-long-only
            # is the real bound for sells (can't sell more than held, T6).
            return None
        current_exposure = sum(p.market_value for p in positions)
        total = current_exposure + preview.estimated_cost
        if total > self._config.capital.pilot_amount_usd:
            return Refusal(
                gate="capital-ceiling",
                reason=(
                    f"estimated cost {preview.estimated_cost:.2f} + current exposure "
                    f"{current_exposure:.2f} = {total:.2f} exceeds pilot capital "
                    f"{self._config.capital.pilot_amount_usd:.2f}"
                ),
                state={
                    "estimated_cost": preview.estimated_cost,
                    "current_exposure": current_exposure,
                    "pilot_amount_usd": self._config.capital.pilot_amount_usd,
                },
            )
        return None

    def _check_per_trade_cap(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        if order.order_action is not OrderAction.BUY:
            return None  # see _check_capital_ceiling: sells never need a sizing gate
        cap_usd = (self._config.caps.per_trade_pct / 100.0) * self._config.capital.pilot_amount_usd
        if preview.estimated_cost > cap_usd:
            return Refusal(
                gate="per-trade-cap",
                reason=(
                    f"estimated cost {preview.estimated_cost:.2f} exceeds the per-trade cap "
                    f"{cap_usd:.2f} ({self._config.caps.per_trade_pct}% of pilot capital)"
                ),
                state={
                    "estimated_cost": preview.estimated_cost,
                    "cap_usd": cap_usd,
                    "per_trade_pct": self._config.caps.per_trade_pct,
                },
            )
        return None

    # -- fail closed --------------------------------------------------------

    def _fail_closed_refusal(self, exc: Exception) -> Refusal:
        return Refusal(
            gate="internal-error",
            reason=f"safety gate check failed unexpectedly, refusing (fail closed): {exc}",
            state={"exception_type": type(exc).__name__},
        )


class PassthroughGate:
    """Phase-1-ONLY non-enforcing gate (ADR-0002). Enforces nothing — every
    check always allows. Exists so the six tools (incl. preview_order/place_order)
    can be hand-tested against sandbox before Phase 2's real cap logic exists
    (SPEC §7 Phase 1 deliverable). The call sites (T1) are wired in
    `server/tools.py` regardless — only the gate's *decision* is a no-op here.

    Safety net while this is in use: `server/app.py::create_app` hard-refuses to
    start outside `environment.mode == "sandbox"`, so this can never front a real
    order. Phase 2 replaces this with `ConfiguredSafetyGate` — the cap wall
    (tests/wall/) forces that swap before caps are considered live.
    """

    def check_preview(self, order: OrderRequest) -> Refusal | None:
        return None

    def check_priced_preview(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        return None

    def check_place(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        return None


def preview_required_refusal(preview_id: str) -> Refusal:
    """Gate `preview-required` (SPEC §4.2, T2): `place_order` referenced a
    preview_id with no live binding in this run's PreviewStore. Authored here
    (not in the tool handler) so the refusal payload is defined alongside every
    other gate — the handler only does the dict lookup (server/tools.py)."""
    return Refusal(
        gate="preview-required",
        reason="place_order references no live preview from this run",
        state={"preview_id": preview_id},
    )
