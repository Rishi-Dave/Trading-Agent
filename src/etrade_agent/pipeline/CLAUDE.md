# pipeline/ — adds to root CLAUDE.md; only what differs here

- Shape decided by the Phase 3 spike (ADR-0004): analyst steps (news/technical/
  fundamental) -> deterministic `AggregatorStep` -> `TraderStep` -> advisory risk
  (`CapsMirrorRiskStep` + `RiskAdvisorStep`), composed as a plain list via
  `run_pipeline` — no framework dependency (no LangGraph), no debate loop, no investor
  personas (deferred, per SPEC §6 "start simple").
- This module proposes; the server disposes (T1). Never import `server/` from here;
  `CapsMirrorRiskStep`/`RiskAdvisorStep` are advisory and duplicated by design — the
  enforcing copy lives in `server/safety.py`. Neither step may refuse an order or
  mutate a `Decision`; they only annotate `context.notes`.
- Every `Decision` must carry `reasoning_summary` and dated `signals` — they become the
  T4 receipts in `trade_log` (via `signals_to_json`). A step that strips or summarizes
  away evidence breaks auditability.
- News/LLM access goes through injected protocols only: `NewsSource` (`news.py`, v1 =
  `WebSearchNewsSource`, kept Finnhub-swappable), `LLMClient` (`llm.py`), `MarketDataSource`
  (`market.py`, read-only — never the order-mutating side of `EtradeClient`). The
  concrete `claude -p`-backed `LLMClient` lives in `runner/llm_client.py` and is wired
  in only by the Phase-4 runner; this module never imports `runner/`.
