"""Phase 3 pipeline wall (SPEC §7 Phase 3 row): "given fixed inputs, Decision
is schema-valid, receipts present, advisory risk check runs."

Deterministic — a fake `LLMClient`/`NewsSource`/`MarketDataSource` stand in
for the real WebSearch/claude-CLI-backed adapters, fed from recorded
responses under `fixtures/pipeline/` (etrade-fixtures recording discipline
extended to non-E*Trade seams, ADR-0004 point 5). No live network, no live
model call, in this wall.

`phase3` marker (conftest.py, this dir) isolates this from the day-one-
blocking caps wall — CI's `safety-wall` job (`-m "wall and not phase1 and
not phase3"`) is unaffected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from etrade_agent.config import AppConfig, load_config
from etrade_agent.etrade.client import PreviewBinding
from etrade_agent.etrade.models import (
    Balance,
    OrderAction,
    OrderPreview,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    Quote,
)
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
    TechnicalAnalystStep,
    TraderStep,
    run_pipeline,
    signals_to_json,
)
from etrade_agent.server import tools
from etrade_agent.server.preview_store import _NO_PIPELINE_REASONING as NO_PIPELINE_REASONING
from etrade_agent.server.preview_store import PreviewStore
from etrade_agent.server.safety import Refusal
from etrade_agent.store import db
from etrade_agent.store.state import StateStore
from tests.conftest import VALID_CONFIG_TOML

_RUN_ID = "wall-phase3-run"

# --- fixed inputs: fixtures/pipeline/ (ADR-0004 point 5) — same naming
# spirit as fixtures/etrade/ (<endpoint>.<discriminator>.<date>.json), scrubbed
# of nothing here since there's no real secret/API data, just recorded canned
# model/news responses standing in for the live WebSearch/claude-CLI seam ----

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "pipeline"


def _fixture_text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


def _fixture_json(name: str) -> object:
    return json.loads(_fixture_text(name))


_NEWS_RESPONSE = _fixture_text("news_analyst.symbol-SPY.2026-07-11.json")
_TECHNICAL_RESPONSE = _fixture_text("technical_analyst.symbol-SPY.2026-07-11.json")
_FUNDAMENTAL_RESPONSE = _fixture_text("fundamental_analyst.symbol-SPY.2026-07-11.json")
_TRADER_RESPONSE = _fixture_text("trader.symbol-SPY.2026-07-11.json")
_RISK_ADVISOR_RESPONSE = _fixture_text("risk_advisor.symbol-SPY.2026-07-11.json")


@dataclass
class FakeLLMClient:
    """Dispatches on the `[step-name]` tag every prompt template in
    `pipeline/steps.py` is prefixed with — no live model call."""

    calls: list[str] = field(default_factory=list)

    def complete(self, prompt: str, *, allowed_tools: list[str] | None = None) -> str:
        self.calls.append(prompt)
        # startswith, not `in`: the trader prompt embeds evidence lines like
        # "- [news-analyst] ..." in its own body, so substring containment
        # would false-positive-match it against the news-analyst response.
        # Every template is prefixed with its own tag, so startswith is exact.
        if prompt.startswith("[news-analyst]"):
            return _NEWS_RESPONSE
        if prompt.startswith("[technical-analyst]"):
            return _TECHNICAL_RESPONSE
        if prompt.startswith("[fundamental-analyst]"):
            return _FUNDAMENTAL_RESPONSE
        if prompt.startswith("[trader]"):
            return _TRADER_RESPONSE
        if prompt.startswith("[risk-advisor]"):
            return _RISK_ADVISOR_RESPONSE
        raise AssertionError(f"unexpected prompt with no fixture response: {prompt!r}")


@dataclass
class FakeNewsSource:
    items: list[NewsItem]

    def headlines(self, symbol: str, since: datetime) -> list[NewsItem]:
        return self.items


@dataclass
class FakeMarketDataSource:
    def get_quote(self, symbol: str) -> Quote:
        return Quote(
            symbol=symbol,
            bid=449.5,
            ask=450.5,
            last=450.0,
            volume=1_000_000,
            as_of=datetime.now(UTC),
        )

    def get_positions(self) -> list[Position]:
        return []

    def get_balances(self) -> Balance:
        return Balance(account_value=1000.0, cash_available=1000.0, buying_power=1000.0)


def _load_news_items(symbol: str) -> list[NewsItem]:
    raw = _fixture_json(f"news_headlines.symbol-{symbol}.2026-07-11.json")
    assert isinstance(raw, list)
    return [
        NewsItem(
            symbol=symbol,
            headline=entry["headline"],
            summary=entry["summary"],
            source="fixture-wire",
            published_at=datetime.fromisoformat(entry["published_at"]),
            url=entry.get("url"),
        )
        for entry in raw
    ]


def _run_full_pipeline() -> PipelineContext:
    llm = FakeLLMClient()
    news = FakeNewsSource(items=_load_news_items("SPY"))
    market = FakeMarketDataSource()
    config = _config()

    steps = [
        NewsAnalystStep(news=news, llm=llm),
        TechnicalAnalystStep(market=market, llm=llm),
        FundamentalAnalystStep(llm=llm),
        AggregatorStep(),
        TraderStep(llm=llm),
        CapsMirrorRiskStep(market=market, config=config),
        RiskAdvisorStep(llm=llm),
    ]
    context = PipelineContext(run_id=_RUN_ID, symbols=["SPY"])
    return run_pipeline(context, steps)


def _config() -> AppConfig:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.toml"
        path.write_text(VALID_CONFIG_TOML)
        return load_config(path)


def _state(tmp_path: Path) -> StateStore:
    conn = db.connect(tmp_path / "trading.db")
    return StateStore(conn)


# --- 1. schema-valid Decision ------------------------------------------------


def test_pipeline_produces_a_schema_valid_decision() -> None:
    context = _run_full_pipeline()

    assert len(context.decisions) == 1
    decision = context.decisions[0]
    assert isinstance(decision, Decision)
    assert decision.action in Action
    assert decision.symbol == "SPY"
    assert decision.quantity >= 0
    assert 0.0 <= decision.confidence <= 1.0


# --- 2. receipts present & genuine (T4) -------------------------------------


def test_decision_carries_genuine_non_placeholder_receipts() -> None:
    context = _run_full_pipeline()
    decision = context.decisions[0]

    assert decision.reasoning_summary
    assert decision.reasoning_summary != NO_PIPELINE_REASONING
    assert len(decision.signals) >= 3  # news + technical + fundamental, at minimum
    # A synthesis, not a verbatim echo of any single analyst's summary.
    assert decision.reasoning_summary not in {s.summary for s in decision.signals}

    for signal in decision.signals:
        assert signal.source
        assert isinstance(signal.as_of, datetime)
        assert signal.summary

    round_tripped = json.loads(signals_to_json(decision.signals))
    assert len(round_tripped) == len(decision.signals)
    for item in round_tripped:
        assert set(item) == {"source", "as_of", "summary", "detail"}
        assert item["summary"]


# --- 3. advisory risk check runs (T1: annotates only, never blocks) --------


def test_advisory_risk_steps_ran_and_only_annotated() -> None:
    context = _run_full_pipeline()
    decision = context.decisions[0]

    assert "risk_advisories" in context.notes
    mirror_advisories = context.notes["risk_advisories"]
    assert len(mirror_advisories) == 1
    assert mirror_advisories[0]["symbol"] == "SPY"
    # T1: this can flag, but decisions themselves are untouched either way.
    assert isinstance(mirror_advisories[0]["flags"], list)

    assert "risk_advisory_llm" in context.notes
    llm_advisories = context.notes["risk_advisory_llm"]
    assert len(llm_advisories) == 1
    assert llm_advisories[0]["symbol"] == "SPY"
    assert llm_advisories[0]["summary"]

    # Neither risk step removed or altered the trader's decision (T1: advisory
    # only, never enforcement).
    assert context.decisions[0] is decision


# --- 4. receipt seam: Decision reasoning reaches trade_log via preview_order
# / place_order, replacing the placeholder for real (ADR-0004) --------------


class _AllowGate:
    def check_preview(self, order: OrderRequest) -> Refusal | None:
        return None

    def check_priced_preview(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        return None

    def check_place(self, preview: OrderPreview, order: OrderRequest) -> Refusal | None:
        return None


@dataclass
class _FakeEtradeClient:
    def preview_order(self, order: OrderRequest) -> tuple[OrderPreview, PreviewBinding]:
        preview = OrderPreview(preview_id="pv-wall", estimated_cost=450.0, warnings=[])
        binding = PreviewBinding(
            preview_ids=[{"previewId": "pv-wall"}],
            order_type="EQ",
            order_block=[{}],
            client_order_id="wall-client-order",
        )
        return preview, binding

    def place_from_binding(self, binding: PreviewBinding) -> OrderStatus:
        return OrderStatus(etrade_order_id="wall-order-1", status="OPEN", filled_quantity=0)


def test_decision_reasoning_reaches_trade_log_through_preview_and_place(tmp_path: Path) -> None:
    context = _run_full_pipeline()
    decision = context.decisions[0]
    assert decision.action == Action.BUY and decision.quantity > 0, (
        "fixed fixture inputs (this file's canned trader response) are expected to "
        "produce a placeable BUY — if this fails, the fixture response changed"
    )

    config = _config()
    state = _state(tmp_path)
    store = PreviewStore()
    gate = _AllowGate()
    client = _FakeEtradeClient()

    order = OrderRequest(
        symbol=decision.symbol,
        order_action=OrderAction.BUY,
        quantity=decision.quantity,
        order_type=OrderType.MARKET,
    )
    reasoning_summary = decision.reasoning_summary
    signals_json = signals_to_json(decision.signals)

    preview_result = tools.preview_order(
        client,
        gate,
        store,
        state,
        config,
        _RUN_ID,
        order,
        reasoning_summary=reasoning_summary,
        signals_json=signals_json,
    )
    assert "refused" not in preview_result

    place_result = tools.place_order(
        client, gate, store, state, config, _RUN_ID, preview_result["preview_id"]
    )
    assert "refused" not in place_result

    row = state.conn.execute(
        "SELECT reasoning_summary, signals_json, executed FROM trade_log"
    ).fetchone()
    assert row is not None
    db_reasoning, db_signals_json, executed = row
    assert executed == 1
    assert db_reasoning == reasoning_summary
    assert db_reasoning != NO_PIPELINE_REASONING
    assert db_signals_json == signals_json
    assert db_signals_json != "[]"
