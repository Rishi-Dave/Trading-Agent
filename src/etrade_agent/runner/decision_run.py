"""Decision-run orchestration loop (SPEC §7 Phase 4, §9): fetch state ->
pipeline -> execute-within-caps -> log -> notify.

Runner shape (SPEC §3.1 Step 0 #2, ADR-0005): direct in-process. This module
imports `server.tools`/`server.app.Runtime`/`pipeline.steps` and drives the
same `preview_order`/`place_order` functions the MCP server registers as
tools — built from the SAME `server.app.build_runtime()` construction path,
so this loop and the interactive MCP server always enforce through one
`ConfiguredSafetyGate`, never a second, divergently-built copy (T1). Nothing
here bypasses the gate: every order attempt goes through `preview_order` then
`place_order`, exactly like a live `claude -p` agent calling the MCP tools
would (T1/T2).

The `Decision` -> `OrderRequest` mapping (`_decision_to_order`) is the one
place a bug could turn a HOLD or a malformed decision into a silent order
attempt — see its docstring. Non-whitelisted symbols are NOT filtered here;
they become real order attempts the gate refuses (a `trade_log` refusal
receipt), which is the correct non-silent path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from etrade_agent import logs
from etrade_agent.etrade.models import (
    OrderAction,
    OrderRequest,
    OrderType,
    SecurityType,
    unrealized_pnl,
)
from etrade_agent.notify.ntfy import NotifyFn
from etrade_agent.pipeline.llm import LLMClient
from etrade_agent.pipeline.news import NewsSource
from etrade_agent.pipeline.steps import (
    Action,
    AggregatorStep,
    CapsMirrorRiskStep,
    Decision,
    FundamentalAnalystStep,
    NewsAnalystStep,
    PipelineContext,
    PipelineStep,
    RiskAdvisorStep,
    TechnicalAnalystStep,
    TraderStep,
    run_pipeline,
    signals_to_json,
)
from etrade_agent.runner.status import build_status_report, write_status_report_best_effort
from etrade_agent.server import tools
from etrade_agent.server.app import Runtime
from etrade_agent.store.state import today_utc

_AGENT_ID = "etrade-runner"


@dataclass(frozen=True)
class OrderOutcome:
    """One decision's order attempt, after preview_order/place_order ran."""

    symbol: str
    action: str
    executed: bool
    refusal_gate: str | None
    etrade_order_id: str | None = None


@dataclass
class RunSummary:
    """What one decision run did — the shape a notify/status report reads
    from. `orders_skipped` covers every Decision that never became an order
    attempt (HOLD, zero/negative quantity, a malformed symbol) — distinct
    from `outcomes`, which is only decisions that actually reached the gate."""

    run_id: str
    decisions_considered: int
    orders_skipped: int
    outcomes: list[OrderOutcome] = field(default_factory=list)

    @property
    def executed_count(self) -> int:
        return sum(1 for o in self.outcomes if o.executed)

    @property
    def refused_count(self) -> int:
        return sum(1 for o in self.outcomes if not o.executed)


def _decision_to_order(decision: Decision) -> OrderRequest | None:
    """HOLD decisions and non-positive quantities never become order
    attempts — this is exactly where a mapping bug would hide a silent
    order (standing warning, Phase 4 kickoff). A malformed decision (e.g. an
    empty symbol) also maps to None rather than raising, since one bad
    Decision must not abort the whole run's remaining decisions.

    Deliberately NOT checked here: whitelist membership. A non-whitelisted
    symbol becomes a real order attempt that `preview_order`'s `whitelist`
    gate refuses — producing a trade_log refusal receipt — which is the
    correct non-silent path, not this function's job to preempt.
    """
    if decision.action is Action.HOLD:
        return None
    if decision.quantity <= 0:
        logs.log(
            _AGENT_ID,
            "warning",
            f"decision for {decision.symbol} has non-positive quantity, skipping",
            symbol=decision.symbol,
            action=decision.action.value,
            quantity=decision.quantity,
        )
        return None
    try:
        return OrderRequest(
            symbol=decision.symbol,
            order_action=OrderAction(decision.action.value),
            quantity=decision.quantity,
            security_type=SecurityType.EQ,
            order_type=OrderType.MARKET,
        )
    except ValidationError as exc:
        logs.log(
            _AGENT_ID,
            "warning",
            f"decision for {decision.symbol!r} did not map to a valid order, skipping",
            symbol=decision.symbol,
            action=decision.action.value,
            quantity=decision.quantity,
            error=str(exc),
        )
        return None


def execute_decisions(
    rt: Runtime,
    decisions: list[Decision],
    *,
    notify: NotifyFn,
) -> RunSummary:
    """Maps each Decision to an order attempt (or a skip) and drives it
    through the real preview_order/place_order safety-gated path (T1/T2) —
    the unit the run wall exercises directly with hand-built Decisions to
    prove "executes <= caps" against the REAL gate, not a stand-in."""
    outcomes: list[OrderOutcome] = []
    orders_skipped = 0

    for decision in decisions:
        order = _decision_to_order(decision)
        if order is None:
            orders_skipped += 1
            continue

        reasoning_summary = decision.reasoning_summary
        signals_json = signals_to_json(decision.signals)

        preview_result = tools.preview_order(
            rt.client,
            rt.gate,
            rt.store,
            rt.state,
            rt.config,
            rt.run_id,
            order,
            reasoning_summary=reasoning_summary,
            signals_json=signals_json,
        )
        if preview_result.get("refused"):
            outcomes.append(
                OrderOutcome(
                    symbol=order.symbol,
                    action=order.order_action.value,
                    executed=False,
                    refusal_gate=preview_result.get("gate"),
                )
            )
            continue

        place_result = tools.place_order(
            rt.client,
            rt.gate,
            rt.store,
            rt.state,
            rt.config,
            rt.run_id,
            preview_result["preview_id"],
        )
        if place_result.get("refused"):
            outcomes.append(
                OrderOutcome(
                    symbol=order.symbol,
                    action=order.order_action.value,
                    executed=False,
                    refusal_gate=place_result.get("gate"),
                )
            )
            continue

        outcomes.append(
            OrderOutcome(
                symbol=order.symbol,
                action=order.order_action.value,
                executed=True,
                refusal_gate=None,
                etrade_order_id=place_result.get("etrade_order_id"),
            )
        )
        notify(
            f"Trade executed: {order.order_action.value} {order.quantity} {order.symbol}",
            reasoning_summary,
        )

    return RunSummary(
        run_id=rt.run_id,
        decisions_considered=len(decisions),
        orders_skipped=orders_skipped,
        outcomes=outcomes,
    )


def _log_advisory_notes(run_id: str, notes: dict[str, Any], *, log_dir: Path | None) -> None:
    """Phase 3 open thread (PHASE3-REPORT.md, ADR-0005 Step 0 #4):
    `context.notes` (aggregate, risk_advisories, risk_advisory_llm) had no
    durable storage path — a high-concern flag on an executed trade could
    evaporate at the end of the pipeline run with no record anywhere. This
    is the runner's "log" step (fetch -> pipeline -> execute -> log ->
    notify): every run's notes get one durable JSONL line, keyed by run_id."""
    logs.log(
        _AGENT_ID,
        "info",
        "pipeline advisory notes",
        log_dir=log_dir,
        run_id=run_id,
        notes=notes,
    )


def run_decision(
    rt: Runtime,
    *,
    llm: LLMClient,
    news: NewsSource,
    notify: NotifyFn,
    log_dir: Path | None = None,
    status_dir: Path | None = None,
) -> RunSummary | None:
    """The full fetch -> pipeline -> execute -> log -> notify loop (SPEC §9).

    Preflight: if the kill switch is engaged, the run is skipped entirely —
    before spending any LLM/WebSearch calls. This is an optimization only;
    ConfiguredSafetyGate.check_place still refuses on kill-switch regardless
    (T1) even if this preflight were ever bypassed or wrong.

    `rt.client` (an EtradeClient) is passed as the pipeline's MarketDataSource
    seam (get_quote/get_positions/get_balances) — it satisfies that Protocol
    structurally, per pipeline/market.py, with no changes needed there.

    `status_dir`, like `log_dir`, is optional (Phase 5, SPEC §9): when given,
    a status/<run_id>.json report is written on every path this function can
    return through, best-effort — an observability failure here must never
    turn an otherwise-successful (or already-skipped) run into a failure
    (ADR-0006 Step 0 #2).
    """
    start = time.monotonic()
    if rt.state.is_kill_engaged():
        logs.log(
            _AGENT_ID, "warning", "kill switch engaged; skipping decision run", run_id=rt.run_id
        )
        notify("Decision run skipped", f"kill switch is engaged (run {rt.run_id})")
        if status_dir is not None:
            report = build_status_report(
                rt.run_id,
                None,
                stage="skipped-kill-switch",
                duration_seconds=time.monotonic() - start,
            )
            write_status_report_best_effort(status_dir, rt.run_id, report)
        return None

    symbols = sorted(rt.config.whitelist.enabled_symbols())
    steps: list[PipelineStep] = [
        NewsAnalystStep(news=news, llm=llm),
        TechnicalAnalystStep(market=rt.client, llm=llm),
        FundamentalAnalystStep(llm=llm),
        AggregatorStep(),
        TraderStep(llm=llm),
        CapsMirrorRiskStep(market=rt.client, config=rt.config),
        RiskAdvisorStep(llm=llm),
    ]
    context = PipelineContext(run_id=rt.run_id, symbols=symbols)
    context = run_pipeline(context, steps)

    summary = execute_decisions(rt, context.decisions, notify=notify)

    _log_advisory_notes(rt.run_id, context.notes, log_dir=log_dir)

    notify(
        f"Decision run complete ({rt.run_id})",
        f"{summary.executed_count} executed, {summary.refused_count} refused, "
        f"{summary.orders_skipped} skipped (of {summary.decisions_considered} decisions)",
    )

    try:
        _send_daily_digest(rt, notify)
    except Exception as exc:  # observability must never fail an otherwise-successful run
        logs.log(_AGENT_ID, "warning", "daily digest failed to build/send", error=str(exc))

    if status_dir is not None:
        report = build_status_report(
            rt.run_id, summary, stage="completed", duration_seconds=time.monotonic() - start
        )
        write_status_report_best_effort(status_dir, rt.run_id, report)

    return summary


def _send_daily_digest(rt: Runtime, notify: NotifyFn) -> None:
    """SPEC §9's daily digest (trades, P&L, caps remaining) — ADR-0006 Step 0
    #1: store-backed, not the in-memory RunSummary, so a day with a re-run or
    a manual `.mcp.json` place_order still produces an accurate day-level
    number, and fired at the end of run_decision's happy path (cheapest
    trigger point, reuses this run's already-open Runtime).

    P&L (ADR-0006 Step 0 #4): reports LIVE unrealized P&L (the identical
    calculation the loss-breaker gate itself uses, etrade.models.unrealized_pnl)
    labeled as such, and states plainly that realized P&L isn't tracked yet
    (ADR-0005 point 3) rather than printing a misleading $0.00.
    """
    day = today_utc()
    snapshot = rt.state.read_caps_state(day)
    trades_remaining = max(0, rt.config.caps.daily_trade_limit - snapshot.trades_executed)
    unrealized = unrealized_pnl(rt.client.get_positions())
    breaker_state = "TRIPPED" if snapshot.breaker_tripped else "ARMED"
    body = (
        f"Trades executed: {snapshot.trades_executed} / {rt.config.caps.daily_trade_limit} "
        f"({trades_remaining} remaining)\n"
        f"Caps: per-trade {rt.config.caps.per_trade_pct}%, "
        f"daily-loss {rt.config.caps.daily_loss_pct}%\n"
        f"P&L: unrealized ${unrealized:+.2f} (live); realized not yet tracked (ADR-0005)\n"
        f"Breaker: {breaker_state}"
    )
    notify(f"Daily digest ({day})", logs.redact(body))
