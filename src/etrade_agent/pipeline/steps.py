"""Shape-agnostic pipeline contracts (SPEC §6) + the Phase 3 spike's concrete
step graph (ADR-0004): analyst steps (news/technical/fundamental) -> a
deterministic aggregator -> a trader -> advisory risk steps. The pipeline
PROPOSES; the server DISPOSES (T1) — nothing here enforces anything; the
advisory risk steps only annotate `context.notes`, never refuse or mutate a
`Decision`.

Steps compose as a plain list via `run_pipeline` (SPEC §6) — no framework
dependency (no LangGraph, no hard-coded role graph beyond what this module
wires up as one *example* composition).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from etrade_agent import logs
from etrade_agent.config import AppConfig
from etrade_agent.pipeline.llm import LLMClient
from etrade_agent.pipeline.market import MarketDataSource
from etrade_agent.pipeline.news import NewsSource

_AGENT_ID = "etrade-pipeline"


class Action(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Signal:
    """One dated piece of evidence a decision rests on — becomes a T4 receipt."""

    source: str
    as_of: datetime
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    """Pipeline output. reasoning_summary + signals flow into trade_log (T4)."""

    action: Action
    symbol: str
    quantity: int
    confidence: float
    reasoning_summary: str
    signals: tuple[Signal, ...]


@dataclass
class PipelineContext:
    """Mutable bag passed step to step; each role reads and annotates it."""

    run_id: str
    symbols: list[str]
    signals: list[Signal] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)
    decisions: list[Decision] = field(default_factory=list)


class PipelineStep(Protocol):
    """A role (analyst, aggregator, trader, advisory risk check) is just a step."""

    name: str

    def run(self, context: PipelineContext) -> PipelineContext: ...


def run_pipeline(context: PipelineContext, steps: Sequence[PipelineStep]) -> PipelineContext:
    """Plain list composition (SPEC §6) — no framework dependency. Each step
    reads and annotates the same context; step order (analysts before the
    aggregator before the trader before advisory risk) is the caller's to
    define, not this function's to enforce."""
    for step in steps:
        context = step.run(context)
    return context


def signals_to_json(signals: Sequence[Signal]) -> str:
    """The canonical `trade_log.signals_json` serialization (ADR-0004 point
    4). One evidence item: `{source, as_of (ISO 8601), summary, detail}` —
    `Signal`/`Decision` stay frozen; source-specific structure lives in
    `detail` by convention rather than a typed field."""
    return json.dumps(
        [
            {
                "source": s.source,
                "as_of": s.as_of.isoformat(),
                "summary": s.summary,
                "detail": s.detail,
            }
            for s in signals
        ]
    )


def _log_step_skip(step_name: str, symbol: str, reason: str, **data: Any) -> None:
    """A single symbol's LLM/news/market call failing (transport error,
    timeout) or producing an unusable response must not abort the whole run
    for every other symbol and every downstream step (Important finding,
    Phase 3 review) — log at warning and the caller skips just that symbol."""
    logs.log(
        _AGENT_ID,
        "warning",
        f"{step_name} skipped {symbol}: {reason}",
        symbol=symbol,
        **data,
    )


# --- shared LLM -> structured-output parsing (ADR-0004 point 1: analysts and
# risk-advisor differ by prompt/domain, not by code path) -------------------


def _query_llm_signal(
    llm: LLMClient,
    prompt: str,
    *,
    source: str,
    as_of: datetime,
    allowed_tools: list[str] | None = None,
    extra_detail: dict[str, Any] | None = None,
) -> Signal | None:
    """Expects the model to answer with a JSON object
    `{"summary": str, "detail": {...}}`. Any other shape (non-JSON, wrong
    type, missing `summary`) yields no signal — a step that can't produce
    usable evidence must produce nothing, never a fabricated or crashing
    one. `extra_detail` (factual metadata from code, e.g. `symbol`) is
    layered OVER the model's own `detail` keys so a hallucinated (or, for
    WebSearch-backed steps, prompt-injected) field can never overwrite
    ground truth we already know — `detail["symbol"]` in particular drives
    `_signals_by_symbol`'s grouping, so letting the model win here would
    silently reroute evidence to the wrong symbol's Decision (T4)."""
    raw = llm.complete(prompt, allowed_tools=allowed_tools)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("summary"), str):
        return None
    model_detail = parsed.get("detail", {})
    if not isinstance(model_detail, dict):
        model_detail = {}
    detail = {**model_detail, **(extra_detail or {})}
    return Signal(source=source, as_of=as_of, summary=parsed["summary"], detail=detail)


# --- analyst steps -----------------------------------------------------------

_NEWS_PROMPT = (
    "[news-analyst] You are a news/sentiment analyst for {symbol}. Given these "
    "recent headlines, respond with ONLY a JSON object "
    '{{"summary": <one-sentence sentiment read>, '
    '"detail": {{"sentiment": "bullish"|"bearish"|"neutral"}}}}.\n\n'
    "Headlines:\n{headlines}"
)


@dataclass
class NewsAnalystStep:
    """Reads recent headlines via the injected `NewsSource`, asks the LLM to
    characterize sentiment. Produces at most one `Signal` per symbol (skips
    symbols with no headlines — no evidence, no fabricated read)."""

    news: NewsSource
    llm: LLMClient
    name: str = "news-analyst"
    lookback: timedelta = field(default_factory=lambda: timedelta(days=3))

    def run(self, context: PipelineContext) -> PipelineContext:
        now = datetime.now(UTC)
        since = now - self.lookback
        for symbol in context.symbols:
            try:
                items = self.news.headlines(symbol, since)
                if not items:
                    continue
                headlines_text = "\n".join(
                    f"- {i.published_at.date()} {i.headline}: {i.summary}" for i in items
                )
                prompt = _NEWS_PROMPT.format(symbol=symbol, headlines=headlines_text)
                signal = _query_llm_signal(
                    self.llm,
                    prompt,
                    source=self.name,
                    as_of=now,
                    extra_detail={
                        "symbol": symbol,
                        "headline_count": len(items),
                        "urls": [i.url for i in items if i.url],
                    },
                )
            except Exception as exc:  # a flaky news/LLM call must not abort the whole run
                _log_step_skip(self.name, symbol, "news/LLM call raised", error=str(exc))
                continue
            if signal is not None:
                context.signals.append(signal)
        return context


_TECHNICAL_PROMPT = (
    "[technical-analyst] You are a technical analyst for {symbol}. Given this "
    "live quote snapshot (bid={bid}, ask={ask}, last={last}, volume={volume}), "
    "respond with ONLY a JSON object "
    '{{"summary": <one-sentence technical read>, '
    '"detail": {{"bias": "bullish"|"bearish"|"neutral"}}}}.'
)


@dataclass
class TechnicalAnalystStep:
    """Reads a live quote snapshot via the injected `MarketDataSource`.
    Limited to what `Quote` (bid/ask/last/volume) carries — v1 has no
    historical-bar client, so indicators like moving averages/RSI are a
    future extension, not this phase's scope."""

    market: MarketDataSource
    llm: LLMClient
    name: str = "technical-analyst"

    def run(self, context: PipelineContext) -> PipelineContext:
        now = datetime.now(UTC)
        for symbol in context.symbols:
            try:
                quote = self.market.get_quote(symbol)
                prompt = _TECHNICAL_PROMPT.format(
                    symbol=symbol,
                    bid=quote.bid,
                    ask=quote.ask,
                    last=quote.last,
                    volume=quote.volume,
                )
                signal = _query_llm_signal(
                    self.llm,
                    prompt,
                    source=self.name,
                    as_of=now,
                    extra_detail={"symbol": symbol, "last": quote.last, "volume": quote.volume},
                )
            except Exception as exc:  # a flaky quote/LLM call must not abort the whole run
                _log_step_skip(self.name, symbol, "quote/LLM call raised", error=str(exc))
                continue
            if signal is not None:
                context.signals.append(signal)
        return context


_FUNDAMENTAL_PROMPT = (
    "[fundamental-analyst] You are a fundamental analyst for {symbol}. Use web "
    "search to find recent earnings, valuation, and balance-sheet context. "
    "Respond with ONLY a JSON object "
    '{{"summary": <one-sentence fundamental read>, '
    '"detail": {{"outlook": "bullish"|"bearish"|"neutral"}}}}.'
)


@dataclass
class FundamentalAnalystStep:
    """WebSearch-backed — no `NewsSource`/`MarketDataSource` dependency, just
    the injected `LLMClient` with `allowed_tools=["WebSearch"]`."""

    llm: LLMClient
    name: str = "fundamental-analyst"

    def run(self, context: PipelineContext) -> PipelineContext:
        now = datetime.now(UTC)
        for symbol in context.symbols:
            try:
                prompt = _FUNDAMENTAL_PROMPT.format(symbol=symbol)
                signal = _query_llm_signal(
                    self.llm,
                    prompt,
                    source=self.name,
                    as_of=now,
                    allowed_tools=["WebSearch"],
                    extra_detail={"symbol": symbol},
                )
            except Exception as exc:  # a flaky WebSearch/LLM call must not abort the whole run
                _log_step_skip(self.name, symbol, "WebSearch/LLM call raised", error=str(exc))
                continue
            if signal is not None:
                context.signals.append(signal)
        return context


# --- deterministic aggregator -----------------------------------------------


def _signals_by_symbol(signals: list[Signal]) -> dict[str, list[Signal]]:
    by_symbol: dict[str, list[Signal]] = defaultdict(list)
    for signal in signals:
        symbol = signal.detail.get("symbol")
        if isinstance(symbol, str) and symbol:
            by_symbol[symbol].append(signal)
    return by_symbol


@dataclass
class AggregatorStep:
    """Deterministic — no LLM turn (ADR-0004 point 1). Groups this run's
    signals by symbol into `context.notes["aggregate"]` so the trader has one
    consolidated view instead of a flat list. The extension point for adding
    analysts later: any step that appends `Signal`s (with `detail["symbol"]`
    set) before this runs is picked up automatically, no wiring change here."""

    name: str = "aggregator"

    def run(self, context: PipelineContext) -> PipelineContext:
        by_symbol = _signals_by_symbol(context.signals)
        context.notes["aggregate"] = {
            symbol: {
                "signal_count": len(sigs),
                "sources": sorted({s.source for s in sigs}),
            }
            for symbol, sigs in by_symbol.items()
        }
        return context


# --- trader ------------------------------------------------------------------

_TRADER_PROMPT = (
    "[trader] You are the trader for {symbol}. Given this aggregated analyst "
    "read: {aggregate}\n\nAnd this evidence:\n{evidence}\n\n"
    "Decide BUY, SELL, or HOLD. Respond with ONLY a JSON object "
    '{{"action": "BUY"|"SELL"|"HOLD", "quantity": <int >= 0>, '
    '"confidence": <float 0-1>, "reasoning": <string>}}. quantity=0 for HOLD. '
    "This proposal is advisory only — the server enforces all position sizing "
    "and risk caps independently (T1); do not assume this quantity will be "
    "honored as-is."
)


def _parse_trader_decision(raw: str, *, symbol: str, signals: list[Signal]) -> Decision | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    action_raw = parsed.get("action")
    if not isinstance(action_raw, str):
        return None
    try:
        action = Action(action_raw)
    except ValueError:
        return None
    quantity = parsed.get("quantity")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
        return None
    confidence = parsed.get("confidence")
    if not isinstance(confidence, int | float) or isinstance(confidence, bool):
        return None
    if not (0.0 <= float(confidence) <= 1.0):
        return None
    reasoning = parsed.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning:
        return None
    return Decision(
        action=action,
        symbol=symbol,
        quantity=quantity,
        confidence=float(confidence),
        reasoning_summary=reasoning,
        signals=tuple(signals),
    )


@dataclass
class TraderStep:
    """Synthesizes each symbol's signals + the aggregate view into a
    `Decision` — one call per symbol. `reasoning_summary` and `signals`
    become the T4 receipts once this Decision reaches `trade_log`."""

    llm: LLMClient
    name: str = "trader"

    def run(self, context: PipelineContext) -> PipelineContext:
        by_symbol = _signals_by_symbol(context.signals)
        for symbol in context.symbols:
            symbol_signals = by_symbol.get(symbol, [])
            aggregate = context.notes.get("aggregate", {}).get(symbol, {})
            evidence = (
                "\n".join(f"- [{s.source}] {s.summary}" for s in symbol_signals)
                or "(no evidence gathered)"
            )
            prompt = _TRADER_PROMPT.format(
                symbol=symbol, aggregate=json.dumps(aggregate), evidence=evidence
            )
            try:
                raw = self.llm.complete(prompt)
            except Exception as exc:  # a flaky LLM call must not abort the whole run
                _log_step_skip(self.name, symbol, "LLM call raised", error=str(exc))
                continue
            decision = _parse_trader_decision(raw, symbol=symbol, signals=symbol_signals)
            if decision is None:
                # Distinct from a transport failure: the model answered, but not
                # usably — worth its own log line so trade_log's silence on this
                # symbol today is distinguishable from "no evidence to trade on"
                # rather than "the trader's response didn't parse" (Phase 3 review).
                _log_step_skip(
                    self.name, symbol, "response was not a usable decision", raw_response=raw[:500]
                )
                continue
            context.decisions.append(decision)
        return context


# --- advisory risk (T1: both steps ONLY annotate; neither can refuse or
# mutate a Decision — server/safety.py remains the sole enforcement point) --


@dataclass
class CapsMirrorRiskStep:
    """Deterministic advisory mirror of `server/safety.py`'s caps/whitelist
    gates (ADR-0004 point 3). Duplicated BY DESIGN (`pipeline/CLAUDE.md`) —
    never imports `server/safety`, never removes or mutates a `Decision`,
    only appends `context.notes["risk_advisories"]`. A market-data hiccup
    degrades to a flag, never an exception — this step must never be the
    reason a run crashes."""

    market: MarketDataSource
    config: AppConfig
    name: str = "caps-mirror-risk"

    def run(self, context: PipelineContext) -> PipelineContext:
        whitelist = self.config.whitelist.enabled_symbols()
        advisories: list[dict[str, Any]] = []
        for decision in context.decisions:
            flags: list[str] = []
            if decision.symbol not in whitelist:
                flags.append("advisory-not-whitelisted")
            if decision.action == Action.BUY and decision.quantity > 0:
                try:
                    quote = self.market.get_quote(decision.symbol)
                    pilot_amount = self.config.capital.pilot_amount_usd
                    est_cost = quote.last * decision.quantity
                    pct = (est_cost / pilot_amount) * 100 if pilot_amount else 100.0
                    if pct > self.config.caps.per_trade_pct:
                        flags.append("advisory-exceeds-per-trade-pct")
                except Exception:
                    flags.append("advisory-quote-unavailable")
            advisories.append(
                {"symbol": decision.symbol, "action": decision.action.value, "flags": flags}
            )
        context.notes["risk_advisories"] = advisories
        return context


_RISK_ADVISOR_PROMPT = (
    "[risk-advisor] You are a risk advisor reviewing this proposed trade: "
    "{action} {quantity} {symbol} (confidence {confidence}). Reasoning given: "
    "{reasoning}\n\nThis is advisory only — you cannot block or modify the "
    "trade; the server's own caps/whitelist/policy gates are the only "
    "enforcement (T1). Respond with ONLY a JSON object "
    '{{"summary": <one-sentence risk read>, '
    '"detail": {{"concern_level": "low"|"medium"|"high"}}}}.'
)


@dataclass
class RiskAdvisorStep:
    """Qualitative LLM risk read on each proposed `Decision`. `Decision` is
    frozen and already carries its justifying `signals` (T4) — this step
    runs AFTER the trader, so it appends its read to
    `context.notes["risk_advisory_llm"]` rather than mutating the Decision.
    Advisory only (T1): never blocks, never changes `context.decisions`."""

    llm: LLMClient
    name: str = "risk-advisor"

    def run(self, context: PipelineContext) -> PipelineContext:
        now = datetime.now(UTC)
        advisories = list(context.notes.get("risk_advisory_llm", []))
        for decision in context.decisions:
            prompt = _RISK_ADVISOR_PROMPT.format(
                action=decision.action.value,
                quantity=decision.quantity,
                symbol=decision.symbol,
                confidence=decision.confidence,
                reasoning=decision.reasoning_summary,
            )
            try:
                signal = _query_llm_signal(
                    self.llm,
                    prompt,
                    source=self.name,
                    as_of=now,
                    extra_detail={"symbol": decision.symbol},
                )
            except Exception as exc:  # a flaky LLM call must not abort the whole run
                _log_step_skip(self.name, decision.symbol, "LLM call raised", error=str(exc))
                continue
            if signal is not None:
                advisories.append(
                    {"symbol": decision.symbol, "summary": signal.summary, "detail": signal.detail}
                )
        context.notes["risk_advisory_llm"] = advisories
        return context
