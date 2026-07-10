# pipeline/ — adds to root CLAUDE.md; only what differs here

- The pipeline **shape is undecided** pending the Phase 3 spike (SPEC §6: TradingAgents
  vs AI Hedge Fund; ADR required). Until that ADR exists, implement only against the
  protocols in `steps.py` (`PipelineStep`, `Decision`) — no framework dependency
  (no LangGraph), no hard-coded role graph.
- This module proposes; the server disposes (T1). Never import `server/` from here;
  any risk check written here is advisory and duplicated — the enforcing copy lives in
  `server/safety.py`.
- Every `Decision` must carry `reasoning_summary` and dated `signals` — they become the
  T4 receipts in `trade_log`. A step that strips or summarizes away evidence breaks
  auditability.
- News access goes through the `NewsSource` protocol (`news.py`) — v1 is Claude
  WebSearch; keep it swappable (SPEC §6).
