# E*Trade Agentic Trading System — Implementation Plan

**Status:** Draft v1
**Owner:** Rishi
**Scope:** Small-capital, capped, non-approval pilot. Not investment advice — this is a systems/engineering plan, not a trading strategy.

---

## 0. Design decisions locked from prior discussion

- **Broker:** E*Trade, via their REST API (OAuth 1.0a).
- **Approval model:** Capped autonomy, no per-trade approval. Hard caps replace human-in-the-loop.
- **Capital:** Small, fully-loss-tolerant pilot amount, isolated from core holdings/Roth IRA.
- **Compute:** Claude Code / Claude Agent SDK running headless (`claude -p`), authenticated via your Max subscription OAuth token — currently draws from subscription limits, not a separate API bill (the June 15 credit-pool split was paused by Anthropic; revisit this assumption periodically).
- **Decision logic:** Don't design trading strategy from scratch — adapt an existing open-source LLM trading-agent framework's architecture and prompts, since neither of us has deep financial domain expertise. Two strong reference candidates:
  - **TradingAgents** (TauricResearch) — LangGraph-based multi-agent pipeline: analyst agents (fundamentals, sentiment, technicals, news) → bull/bear researcher debate → trader agent → risk manager → portfolio manager. Multi-LLM-provider support including Anthropic. Explicitly framed by the authors as research-only, not financial advice.
  - **AI Hedge Fund** (virattt) — similar multi-agent structure with investor-persona agents (value, growth, etc.) feeding into a risk manager and portfolio manager. Simulation-only by default, which makes it a good source for the *decision pipeline shape* even though you'll swap in real execution.
  - Plan: fork the architectural pattern (agent roles, debate/aggregation structure, prompt scaffolding), not necessarily the code verbatim — your stack is Python/TS + MCP, theirs is LangGraph, so expect a partial rewrite rather than a drop-in.

---

## 1. System architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Claude Code (headless, claude -p, subscription auth)         │
│  Orchestrates: analyst agents → debate → decision → execution │
└───────────────┬─────────────────────────────────────────────┘
                │  MCP tool calls
                ▼
┌─────────────────────────────────────────────────────────────┐
│  etrade-mcp-server (your custom MCP server)                   │
│  Tools: get_quote, get_positions, get_balances,                │
│  preview_order, place_order, get_order_status                 │
│  + Safety layer: caps, circuit breaker, kill switch            │
└───────────────┬─────────────────────────────────────────────┘
                │  OAuth 1.0a REST
                ▼
┌─────────────────────────────────────────────────────────────┐
│  E*Trade API (sandbox → production)                           │
└─────────────────────────────────────────────────────────────┘

Supporting services:
- SQLite store (positions cache, trade log, caps/circuit-breaker state)
- Notification service (ntfy.sh / Telegram / Pushover) — trade-executed pings
- Scheduler (cron or your existing worker infra) — triggers decision runs
```

This mirrors your ff-assistant shape: a deterministic/guarded execution layer underneath, LLM reasoning constrained to analysis and decision-making, not given raw execution authority.

---

## 2. Phased build plan

### Phase 1 — E*Trade MCP server foundation (no LLM yet)
- OAuth 1.0a flow: request token → browser authorize → access token, HMAC-SHA1 signing.
- Token refresh/renewal handling (2hr idle timeout, nightly expiry).
- Build against E*Trade's sandbox first.
- MCP tools: `get_quote`, `get_positions`, `get_balances`, `preview_order`, `place_order`, `get_order_status`.
- **Test by hand** (you calling tools directly via Claude Code interactively) before any autonomous loop touches it.

### Phase 2 — Safety layer (build before any autonomy)
- SQLite schema for: pending caps state, daily trade count, daily P&L snapshot, kill-switch flag.
- Enforce in the server, not the prompt:
  - Capital ceiling (only the funded pilot amount is visible/tradeable)
  - Per-trade size cap (% of account)
  - Daily trade count limit
  - Daily loss circuit breaker (blocks further `place_order` calls once tripped)
  - Symbol whitelist (liquid large-caps only, no leverage/options for v1)
  - Kill switch checked before every `place_order` call
- Write a test suite that tries to violate each cap and confirms the server refuses.

### Phase 3 — Decision pipeline (adapted from open-source reference)
- Port the *shape* of TradingAgents/AI Hedge Fund's pipeline:
  1. Analyst agents gather signals (price/technicals via E*Trade quote data, news/sentiment via a news API or search)
  2. Bull/bear-style debate or a simpler single aggregating step (start simple — you can add debate rounds later once you trust the base loop)
  3. Trader agent proposes an order
  4. Risk manager agent checks the proposal against your caps *before* it ever reaches the MCP server (belt-and-suspenders — the MCP server is the real enforcement, this is a second check)
  5. Portfolio manager agent finalizes and calls `place_order`
- Keep prompts simple and inspectable at first — log every agent's reasoning to your trade log so you can audit *why* it did something.

### Phase 4 — Orchestration & scheduling
- Headless invocation via `claude -p` or Claude Agent SDK, subscription-authenticated.
- Scheduler triggers a decision run at a fixed cadence (e.g. once daily at market open, not continuous — lower frequency = easier to reason about for a pilot).
- Each run: fetch state → run pipeline → execute within caps → log → notify.

### Phase 5 — Notifications & observability
- Push notification (ntfy/Telegram/Pushover) on every executed trade — ticker, side, qty, price, reasoning summary.
- Separate alert if the circuit breaker trips or kill switch engages.
- Simple daily digest: trades made, P&L, caps remaining.

### Phase 6 — Pilot run
- Fixed 2–4 week evaluation window, defined *before* starting.
- Benchmark against SPY or a simple buy-and-hold over the same window — "performance" needs a comparison point, not just raw P&L.
- Decision gate at the end: scale capital, keep flat, adjust the pipeline, or shut down.

---

## 3. Open items / decisions still needed

- [ ] Which reference framework to fork the architecture from — TradingAgents (more elaborate debate structure) vs. AI Hedge Fund (simpler, persona-based)? Worth skimming both repos before committing.
- [ ] News/sentiment data source (E*Trade's API is thin on this — likely need a separate feed).
- [ ] Exact cap numbers (per-trade %, daily trade count, daily loss %) — pick before Phase 2.
- [ ] Symbol whitelist — how many tickers, which ones.
- [ ] Where the SQLite store and scheduler live — same hosted worker as ff-assistant, or separate isolated service (recommend separate, given this touches real money — don't want a bug in one system taking down or corrupting the other).

---

## 4. Notes / caveats

- This plan is systems architecture, not a trading strategy — the actual signal quality of the decision pipeline is unproven and should be treated as experimental.
- Neither TradingAgents nor AI Hedge Fund's authors position their frameworks as production-ready financial advice tools; both are explicit that performance is backtest/research-oriented and not guaranteed to generalize to live markets.
- Keep the pilot capital genuinely disposable for the full evaluation window — the point of Phase 6 is honest signal, which only works if you're not tempted to intervene mid-run.
