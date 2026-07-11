"""Concrete pipeline steps (ADR-0004), tested in isolation via hand-built
PipelineContexts — tests/wall/phase3/ is what exercises the full step chain
together against fixed inputs."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from etrade_agent.config import AppConfig, load_config
from etrade_agent.etrade.models import Balance, Position, Quote
from etrade_agent.pipeline.news import NewsItem
from etrade_agent.pipeline.steps import (
    Action,
    AggregatorStep,
    CapsMirrorRiskStep,
    Decision,
    FundamentalAnalystStep,
    NewsAnalystStep,
    PipelineContext,
    RiskAdvisorStep,
    Signal,
    TechnicalAnalystStep,
    TraderStep,
    run_pipeline,
    signals_to_json,
)
from tests.conftest import VALID_CONFIG_TOML


@dataclass
class StubLLMClient:
    response: str = "{}"
    calls: list[tuple[str, tuple[str, ...] | None]] = field(default_factory=list)

    def complete(self, prompt: str, *, allowed_tools: list[str] | None = None) -> str:
        self.calls.append((prompt, tuple(allowed_tools) if allowed_tools else None))
        return self.response


@dataclass
class StubNewsSource:
    items: list[NewsItem]

    def headlines(self, symbol: str, since: datetime) -> list[NewsItem]:
        return self.items


@dataclass
class StubMarketDataSource:
    quote: Quote

    def get_quote(self, symbol: str) -> Quote:
        return self.quote

    def get_positions(self) -> list[Position]:
        return []

    def get_balances(self) -> Balance:
        return Balance(account_value=1000.0, cash_available=1000.0, buying_power=1000.0)


class ExplodingMarketDataSource:
    """Satisfies MarketDataSource but raises on get_quote — proves
    CapsMirrorRiskStep degrades to a flag rather than propagating (T1: a
    market-data hiccup must never crash the run)."""

    def get_quote(self, symbol: str) -> Quote:
        raise RuntimeError("boom")

    def get_positions(self) -> list[Position]:
        return []

    def get_balances(self) -> Balance:
        return Balance(account_value=1.0, cash_available=1.0, buying_power=1.0)


class RaisingLLMClient:
    """Satisfies LLMClient but raises on complete() — a flaky claude -p
    call (timeout, nonzero exit) must degrade per-symbol, not abort the
    whole run (Important finding, Phase 3 review)."""

    def complete(self, prompt: str, *, allowed_tools: list[str] | None = None) -> str:
        raise RuntimeError("transport failure")


class RaisingNewsSource:
    """Satisfies NewsSource but raises on headlines()."""

    def headlines(self, symbol: str, since: datetime) -> list[NewsItem]:
        raise RuntimeError("transport failure")


def _context(symbols: list[str] | None = None) -> PipelineContext:
    return PipelineContext(run_id="test-run", symbols=symbols or ["SPY"])


def _quote(symbol: str = "SPY", last: float = 450.0) -> Quote:
    return Quote(
        symbol=symbol,
        bid=last - 0.5,
        ask=last + 0.5,
        last=last,
        volume=1000,
        as_of=datetime.now(UTC),
    )


def _decision(action: Action = Action.BUY, symbol: str = "SPY", quantity: int = 1) -> Decision:
    return Decision(
        action=action,
        symbol=symbol,
        quantity=quantity,
        confidence=0.5,
        reasoning_summary="r",
        signals=(),
    )


def _config() -> AppConfig:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.toml"
        path.write_text(VALID_CONFIG_TOML)
        return load_config(path)


# --- signals_to_json (ADR-0004 point 4) --------------------------------------


def test_signals_to_json_round_trips_empty() -> None:
    assert json.loads(signals_to_json(())) == []


def test_signals_to_json_serializes_source_as_of_summary_detail() -> None:
    now = datetime.now(UTC)
    signal = Signal(source="test", as_of=now, summary="a read", detail={"k": "v"})

    parsed = json.loads(signals_to_json([signal]))

    assert parsed == [
        {"source": "test", "as_of": now.isoformat(), "summary": "a read", "detail": {"k": "v"}}
    ]


# --- NewsAnalystStep ----------------------------------------------------------


def test_news_analyst_skips_symbols_with_no_headlines() -> None:
    llm = StubLLMClient()
    step = NewsAnalystStep(news=StubNewsSource(items=[]), llm=llm)

    result = step.run(_context())

    assert result.signals == []
    assert llm.calls == []  # no LLM turn spent on a symbol with nothing to analyze


def test_news_analyst_appends_signal_from_valid_llm_response() -> None:
    item = NewsItem(
        symbol="SPY", headline="h", summary="s", source="wire", published_at=datetime.now(UTC)
    )
    llm = StubLLMClient(
        response=json.dumps({"summary": "bullish read", "detail": {"sentiment": "bullish"}})
    )
    step = NewsAnalystStep(news=StubNewsSource(items=[item]), llm=llm)

    result = step.run(_context())

    assert len(result.signals) == 1
    signal = result.signals[0]
    assert signal.source == "news-analyst"
    assert signal.summary == "bullish read"
    assert signal.detail["symbol"] == "SPY"
    assert signal.detail["sentiment"] == "bullish"


def test_news_analyst_malformed_llm_response_yields_no_signal() -> None:
    item = NewsItem(
        symbol="SPY", headline="h", summary="s", source="wire", published_at=datetime.now(UTC)
    )
    llm = StubLLMClient(response="not json")
    step = NewsAnalystStep(news=StubNewsSource(items=[item]), llm=llm)

    result = step.run(_context())

    assert result.signals == []


def test_news_analyst_requests_no_extra_tool() -> None:
    """News access already went through NewsSource; the sentiment read over
    already-fetched headlines needs no additional tool access."""
    item = NewsItem(
        symbol="SPY", headline="h", summary="s", source="wire", published_at=datetime.now(UTC)
    )
    llm = StubLLMClient(response=json.dumps({"summary": "x", "detail": {}}))
    step = NewsAnalystStep(news=StubNewsSource(items=[item]), llm=llm)

    step.run(_context())

    assert llm.calls[0][1] is None


def test_news_analyst_code_supplied_symbol_wins_over_hallucinated_model_detail() -> None:
    """Critical fix (Phase 3 review): a model that returns its own "symbol"
    key in `detail` must never override the code-supplied ground truth —
    that key drives AggregatorStep/TraderStep's evidence-to-symbol grouping
    (T4). Previously `{**extra_detail, **model_detail}` let the model win;
    now `{**model_detail, **extra_detail}` layers code-supplied fields on
    top."""
    item = NewsItem(
        symbol="SPY", headline="h", summary="s", source="wire", published_at=datetime.now(UTC)
    )
    llm = StubLLMClient(
        response=json.dumps({"summary": "x", "detail": {"symbol": "AAPL", "sentiment": "bullish"}})
    )
    step = NewsAnalystStep(news=StubNewsSource(items=[item]), llm=llm)

    result = step.run(_context())

    assert result.signals[0].detail["symbol"] == "SPY"  # code-supplied wins, not the model's
    assert (
        result.signals[0].detail["sentiment"] == "bullish"
    )  # model's own non-colliding key survives


def test_news_analyst_degrades_to_skip_symbol_on_news_source_exception() -> None:
    step = NewsAnalystStep(news=RaisingNewsSource(), llm=StubLLMClient())

    result = step.run(_context())  # must not raise

    assert result.signals == []


def test_news_analyst_degrades_to_skip_symbol_on_llm_exception() -> None:
    item = NewsItem(
        symbol="SPY", headline="h", summary="s", source="wire", published_at=datetime.now(UTC)
    )
    step = NewsAnalystStep(news=StubNewsSource(items=[item]), llm=RaisingLLMClient())

    result = step.run(_context())  # must not raise

    assert result.signals == []


# --- TechnicalAnalystStep -----------------------------------------------------


def test_technical_analyst_appends_signal_with_quote_detail() -> None:
    llm = StubLLMClient(response=json.dumps({"summary": "steady", "detail": {"bias": "bullish"}}))
    step = TechnicalAnalystStep(market=StubMarketDataSource(quote=_quote(last=451.0)), llm=llm)

    result = step.run(_context())

    assert len(result.signals) == 1
    assert result.signals[0].detail["last"] == 451.0
    assert result.signals[0].detail["symbol"] == "SPY"


def test_technical_analyst_degrades_to_skip_symbol_on_market_exception() -> None:
    step = TechnicalAnalystStep(market=ExplodingMarketDataSource(), llm=StubLLMClient())

    result = step.run(_context())  # must not raise

    assert result.signals == []


def test_technical_analyst_degrades_to_skip_symbol_on_llm_exception() -> None:
    step = TechnicalAnalystStep(market=StubMarketDataSource(quote=_quote()), llm=RaisingLLMClient())

    result = step.run(_context())  # must not raise

    assert result.signals == []


# --- FundamentalAnalystStep ---------------------------------------------------


def test_fundamental_analyst_requests_websearch_tool() -> None:
    llm = StubLLMClient(response=json.dumps({"summary": "sound fundamentals", "detail": {}}))
    step = FundamentalAnalystStep(llm=llm)

    step.run(_context())

    assert llm.calls[0][1] == ("WebSearch",)


def test_fundamental_analyst_degrades_to_skip_symbol_on_llm_exception() -> None:
    step = FundamentalAnalystStep(llm=RaisingLLMClient())

    result = step.run(_context())  # must not raise

    assert result.signals == []


# --- AggregatorStep (deterministic) ------------------------------------------


def test_aggregator_groups_signals_by_symbol() -> None:
    now = datetime.now(UTC)
    context = _context(symbols=["SPY", "AAPL"])
    context.signals = [
        Signal(source="a", as_of=now, summary="s1", detail={"symbol": "SPY"}),
        Signal(source="b", as_of=now, summary="s2", detail={"symbol": "SPY"}),
        Signal(source="c", as_of=now, summary="s3", detail={"symbol": "AAPL"}),
    ]

    result = AggregatorStep().run(context)

    assert result.notes["aggregate"]["SPY"]["signal_count"] == 2
    assert result.notes["aggregate"]["SPY"]["sources"] == ["a", "b"]
    assert result.notes["aggregate"]["AAPL"]["signal_count"] == 1


def test_aggregator_ignores_signals_with_no_symbol_in_detail() -> None:
    now = datetime.now(UTC)
    context = _context()
    context.signals = [Signal(source="a", as_of=now, summary="s", detail={})]

    result = AggregatorStep().run(context)

    assert result.notes["aggregate"] == {}


# --- TraderStep ----------------------------------------------------------------


def test_trader_builds_decision_with_its_symbols_signals() -> None:
    now = datetime.now(UTC)
    context = _context()
    context.signals = [Signal(source="a", as_of=now, summary="s", detail={"symbol": "SPY"})]
    llm = StubLLMClient(
        response=json.dumps(
            {"action": "BUY", "quantity": 2, "confidence": 0.6, "reasoning": "synthesis"}
        )
    )

    result = TraderStep(llm=llm).run(context)

    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.action == Action.BUY
    assert decision.quantity == 2
    assert decision.confidence == 0.6
    assert decision.reasoning_summary == "synthesis"
    assert len(decision.signals) == 1


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps({"action": "NOPE", "quantity": 1, "confidence": 0.5, "reasoning": "x"}),
        json.dumps({"action": "BUY", "quantity": -1, "confidence": 0.5, "reasoning": "x"}),
        json.dumps({"action": "BUY", "quantity": 1, "confidence": 1.5, "reasoning": "x"}),
        json.dumps({"action": "BUY", "quantity": 1, "confidence": 0.5, "reasoning": ""}),
        json.dumps({"action": "BUY", "quantity": 1.5, "confidence": 0.5, "reasoning": "x"}),
        json.dumps({"action": "BUY", "quantity": True, "confidence": 0.5, "reasoning": "x"}),
    ],
)
def test_trader_rejects_malformed_or_out_of_range_responses(raw: str) -> None:
    llm = StubLLMClient(response=raw)

    result = TraderStep(llm=llm).run(_context())

    assert result.decisions == []


def test_trader_allows_hold_with_zero_quantity() -> None:
    llm = StubLLMClient(
        response=json.dumps(
            {"action": "HOLD", "quantity": 0, "confidence": 0.3, "reasoning": "no edge"}
        )
    )

    result = TraderStep(llm=llm).run(_context())

    assert result.decisions[0].action == Action.HOLD
    assert result.decisions[0].quantity == 0


def test_trader_degrades_to_skip_symbol_on_llm_exception() -> None:
    result = TraderStep(llm=RaisingLLMClient()).run(_context())  # must not raise

    assert result.decisions == []


def test_trader_logs_when_response_is_not_a_usable_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Minor finding (Phase 3 review): a malformed trader response should be
    distinguishable from "no evidence to trade on" — both currently leave no
    trade_log row, but only the malformed case should log a warning."""
    from etrade_agent import logs

    logged: list[dict[str, object]] = []
    monkeypatch.setattr(logs, "log", lambda *a, **k: logged.append({"args": a, "kwargs": k}) or {})
    llm = StubLLMClient(response="not json")

    result = TraderStep(llm=llm).run(_context())

    assert result.decisions == []
    assert len(logged) == 1
    assert logged[0]["kwargs"]["symbol"] == "SPY"


# --- CapsMirrorRiskStep (deterministic, advisory-only — T1) ------------------


def test_caps_mirror_never_removes_or_mutates_decisions() -> None:
    context = _context()
    context.decisions = [_decision()]
    step = CapsMirrorRiskStep(market=StubMarketDataSource(quote=_quote()), config=_config())

    result = step.run(context)

    assert result.decisions == [_decision()]


def test_caps_mirror_flags_symbol_not_whitelisted() -> None:
    context = _context(symbols=["XYZ"])
    context.decisions = [_decision(symbol="XYZ")]
    step = CapsMirrorRiskStep(
        market=StubMarketDataSource(quote=_quote(symbol="XYZ")), config=_config()
    )

    result = step.run(context)

    assert "advisory-not-whitelisted" in result.notes["risk_advisories"][0]["flags"]


def test_caps_mirror_flags_advisory_exceeds_per_trade_pct() -> None:
    # VALID_CONFIG_TOML: per_trade_pct=10% of pilot_amount_usd=1000 => $100 cap;
    # quote last=450, qty=1 => $450 estimated cost, well over.
    context = _context()
    context.decisions = [_decision(quantity=1)]
    step = CapsMirrorRiskStep(
        market=StubMarketDataSource(quote=_quote(last=450.0)), config=_config()
    )

    result = step.run(context)

    assert "advisory-exceeds-per-trade-pct" in result.notes["risk_advisories"][0]["flags"]


def test_caps_mirror_skips_quote_lookup_for_hold() -> None:
    context = _context()
    context.decisions = [_decision(action=Action.HOLD, quantity=0)]
    step = CapsMirrorRiskStep(market=ExplodingMarketDataSource(), config=_config())

    result = step.run(context)  # must not raise — ExplodingMarketDataSource would if called

    assert result.notes["risk_advisories"][0]["flags"] == []


def test_caps_mirror_degrades_to_flag_on_market_data_exception() -> None:
    context = _context()
    context.decisions = [_decision()]
    step = CapsMirrorRiskStep(market=ExplodingMarketDataSource(), config=_config())

    result = step.run(context)  # must not raise (T1: never crash the run)

    assert "advisory-quote-unavailable" in result.notes["risk_advisories"][0]["flags"]


# --- RiskAdvisorStep (advisory-only, never mutates Decision — T1) -----------


def test_risk_advisor_appends_without_mutating_decisions() -> None:
    context = _context()
    decision = _decision()
    context.decisions = [decision]
    llm = StubLLMClient(
        response=json.dumps({"summary": "contained risk", "detail": {"concern_level": "low"}})
    )

    result = RiskAdvisorStep(llm=llm).run(context)

    assert result.decisions == [decision]
    assert result.notes["risk_advisory_llm"][0]["summary"] == "contained risk"


def test_risk_advisor_malformed_response_yields_no_advisory_entry() -> None:
    context = _context()
    context.decisions = [_decision()]
    llm = StubLLMClient(response="not json")

    result = RiskAdvisorStep(llm=llm).run(context)

    assert result.notes["risk_advisory_llm"] == []


def test_risk_advisor_degrades_to_skip_on_llm_exception() -> None:
    context = _context()
    context.decisions = [_decision()]

    result = RiskAdvisorStep(llm=RaisingLLMClient()).run(context)  # must not raise

    assert result.notes["risk_advisory_llm"] == []
    assert result.decisions == [_decision()]  # still untouched (T1)


# --- run_pipeline (plain list composition, SPEC §6: no framework) ----------


def test_run_pipeline_runs_steps_in_order() -> None:
    calls: list[str] = []

    @dataclass
    class RecordingStep:
        name: str

        def run(self, context: PipelineContext) -> PipelineContext:
            calls.append(self.name)
            return context

    run_pipeline(_context(), [RecordingStep(name="a"), RecordingStep(name="b")])

    assert calls == ["a", "b"]
