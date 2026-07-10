# E*Trade Agentic Trading System — Specification

**Status:** v1 (supersedes the Draft v1 implementation plan; original content redistributed into §§1–10)
**Owner:** Rishi
**Authority:** This file is the single source of truth. CLAUDE.md, skills, tests, and phase prompts cite § numbers rather than restating content. Changes to §2 or §4 require an ADR in the same commit.

---

## §1 Purpose & scope

A small-capital, capped-autonomy pilot that lets a Claude-orchestrated decision pipeline trade a fully-loss-tolerant amount through E*Trade, with every safety control enforced in code.

**Goals**
- Prove the plumbing: OAuth 1.0a → MCP tools → previewed orders → logged trades → notifications, end to end, sandbox first.
- Prove the guardrails: a test suite that tries to violate every cap and confirms the server refuses (§4, §7 Phase 2).
- Produce an honest performance signal: a fixed 2–4 week evaluation window, defined **before** starting, benchmarked against SPY buy-and-hold over the same window. Decision gate at the end: scale capital, keep flat, adjust the pipeline, or shut down.

**Non-goals / exclusions (v1)**
- This is systems engineering, not a trading strategy; signal quality of the pipeline is experimental and unproven.
- No options, no margin, no short selling, no leveraged instruments (policy gates per T6 — structurally present, policy-disabled).
- No per-trade human approval; hard caps replace human-in-the-loop.
- No continuous trading: fixed-cadence decision runs (once daily at market open for the pilot).
- Capital is isolated from core holdings and the Roth IRA, and stays genuinely disposable for the full evaluation window — the Phase 6 signal only works if there is no mid-run intervention.

---

## §2 System Invariants

T1–T6 are load-bearing. A change that violates one is rejected regardless of how useful the feature is.

- **T1 — Safety is enforced in the MCP server, never in the prompt.** Caps, whitelist, circuit breaker, kill switch, and policy gates are code paths in `src/etrade_agent/server/safety.py` that run on every order-mutating tool call. Prompt-side risk checks (e.g. a risk-manager agent) are advisory belt-and-suspenders; a change that moves enforcement into prompt text is a bug even if the prompt is "very clear."

- **T2 — No order reaches E*Trade without a preview.** `place_order` executes only an order previewed through `preview_order` in the same run; the preview result (symbol, side, quantity, estimated cost) is what the safety gate evaluates. There is no direct-place code path.

- **T3 — Secrets never appear in code, logs, fixtures, or transcripts.** OAuth consumer keys and tokens enter only via `.env` / the gitignored `tokens/` store. The JSONL logger redacts known secret values (§9). Fixtures are scrubbed at record time (§5.4). The PreToolUse hook that blocks shell reads of secret material is defense-in-depth, not the enforcement — the real controls are `.gitignore`, log redaction, and fixture scrubbing. A secret printed anywhere is an incident, not a style issue.

- **T4 — Every executed trade carries reasoning receipts.** The `trade_log` row records the pipeline's reasoning summary, the signals consulted, and a snapshot of caps state at decision time (§5.1). A trade whose "why" cannot be reconstructed from the log did not happen correctly.

- **T5 — Caps are explicit or the system refuses to run.** Per-trade %, daily trade count, and daily loss % have no defaults anywhere in the codebase. Missing or invalid caps abort MCP server startup — never a fallback value, never a warn-and-continue.

- **T6 — Long-only cash equities in v1, as policy gates.** The order model carries `security_type` and `order_action` so options/shorts become a future policy-config change plus ADR — but the v1 gates reject anything that is not a long cash-equity order, in the server (per T1).

---

## §3 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Claude Code (headless `claude -p`, Max-subscription auth)    │
│  Orchestrates: analyst agents → aggregation → decision        │
└───────────────┬─────────────────────────────────────────────┘
                │  MCP tool calls (stdio)
                ▼
┌─────────────────────────────────────────────────────────────┐
│  etrade MCP server (src/etrade_agent/server/)                 │
│  Tools: get_quote, get_positions, get_balances,               │
│         preview_order, place_order, get_order_status          │
│  Safety layer (§4): caps, breaker, whitelist, kill switch     │
└───────────────┬─────────────────────────────────────────────┘
                │  OAuth 1.0a REST (HMAC-SHA1)
                ▼
┌─────────────────────────────────────────────────────────────┐
│  E*Trade API (sandbox → production)                           │
└─────────────────────────────────────────────────────────────┘

Supporting: SQLite store (§5.1) · ntfy.sh notifications (§9) · launchd scheduler (§9)
```

### §3.1 Module map (`src/etrade_agent/`)

| Module | Owns | May import |
|---|---|---|
| `config.py` | TOML + env loading, caps validation (T5) | — (leaf) |
| `logs.py` | JSONL structured logging + secret redaction (T3) | — (leaf) |
| `etrade/` | OAuth 1.0a dance/renewal, REST client, API models | `config`, `logs` |
| `store/` | SQLite schema, migrations, state access (§5.1) | `config`, `logs` |
| `server/` | MCP app, tool handlers, **safety layer** (§4) | `etrade`, `store`, `config`, `logs` |
| `pipeline/` | Decision-pipeline protocols, news-source interface (§6) | `config`, `logs`, `etrade/models` |
| `notify/` | ntfy.sh pings (§9) | `config`, `logs` |
| `runner/` | Headless `claude -p` adapter, status reports (§9) | `config`, `logs`, `notify` |

Dependency rules: `pipeline/` never imports `server/` (the pipeline proposes; the server disposes). Nothing imports `runner/`. `etrade/` and `store/` never import `server/` or `pipeline/`.

### §3.2 Isolation

Fully standalone repo. No dependency on Agent-Creation's `shared-py`/`agent_factory` — useful patterns (headless adapter, JSONL logging) are reimplemented here so no refactor of a sibling system can touch the money path.

---

## §4 Safety-layer contract

The safety layer is `server/safety.py`. Every gate below is checked by `place_order` (and where marked, `preview_order`) **before** any E*Trade call. Every gate maps 1:1 to a named wall test (§7). Gates evaluate the *preview result* (T2), not the pipeline's claim.

### §4.1 Refusal shape

A refused call returns an MCP tool error with machine-parseable payload:

```json
{"refused": true, "gate": "<gate-id>", "reason": "<human sentence>", "state": {"...": "relevant snapshot"}}
```

Refusals are logged (JSONL, level=warning) and counted; they never raise unhandled exceptions and never partially execute.

### §4.2 Gates

| Gate id | Rule | Checked at |
|---|---|---|
| `caps-required` | All three caps present and valid at startup, else the server process exits nonzero before registering tools (T5) | startup |
| `kill-switch` | If `kill_switch.engaged`, refuse. Checked first, before every `place_order` | place |
| `capital-ceiling` | Estimated cost of the order + current exposure ≤ configured pilot capital | preview + place |
| `per-trade-cap` | Estimated order cost ≤ `caps.per_trade_pct` % of pilot capital | preview + place |
| `daily-trade-limit` | Executed-trade count today < `caps.daily_trade_limit` | place |
| `loss-breaker` | If realized+unrealized daily P&L ≤ −`caps.daily_loss_pct` %, breaker trips; all further `place_order` refused until reset | place |
| `whitelist` | Symbol ∈ union of `whitelist.enabled_tiers` lists | preview + place |
| `policy-long-only` | `order_action == "BUY"`, or `"SELL"` only up to currently-held quantity (no short) | preview + place |
| `policy-security-type` | `security_type` ∈ `policy.allowed_security_types` (v1: `["EQ"]`) | preview + place |
| `preview-required` | `place_order` must reference a preview id issued this run for an identical order (T2) | place |

### §4.3 State transitions

- **Breaker:** `armed → tripped` when the loss threshold is crossed (event notified, §9). Reset is manual only: a CLI action (`scripts/`) that writes `caps_state`, requires the operator, and is logged + notified. Never auto-resets at market open — a new day requires an explicit reset decision.
- **Kill switch:** `engaged ↔ disengaged` only via manual CLI action; state lives in SQLite (§5.1) so it survives restarts. Engaging also sends a notification. The switch ships **engaged** by default on a fresh database.
- **Caps changes** (numbers, whitelist tiers, policy gates): edit `config/config.toml`, bump `config_version`, ADR in the same commit (§8.2). The server logs the active `config_version` at startup and stamps it on every trade row.

---

## §5 Data contracts

### §5.1 SQLite schema (`store/schema.py`)

Single file DB (path in config; WAL mode). Tables:

- **`trade_log`** — one row per *attempted* order: `id`, `ts_utc`, `run_id`, `config_version`, `symbol`, `order_action`, `security_type`, `quantity`, `preview_id`, `estimated_cost`, `executed` (bool), `refusal_gate` (nullable), `etrade_order_id` (nullable), `reasoning_summary` (T4), `signals_json` (T4), `caps_snapshot_json` (T4).
- **`caps_state`** — keyed by `date_utc`: `trades_executed`, `realized_pnl`, `breaker_tripped` (bool), `breaker_tripped_ts`, `breaker_reset_ts`, `breaker_reset_by`.
- **`kill_switch`** — single row: `engaged` (bool, default **1**), `changed_ts`, `changed_by`, `note`.
- **`positions_cache`** — `symbol`, `quantity`, `cost_basis`, `as_of_ts` (advisory cache; E*Trade is authoritative).
- **`schema_migrations`** — `version`, `applied_ts`. Migrations are forward-only numbered SQL constants in `schema.py`.

### §5.2 MCP tools (`server/tools.py`)

| Tool | Request | Response (success) |
|---|---|---|
| `get_quote` | `symbol` | Quote: `symbol, bid, ask, last, volume, as_of` |
| `get_positions` | — | list of Position: `symbol, quantity, cost_basis, market_value` |
| `get_balances` | — | Balance: `account_value, cash_available, buying_power` |
| `preview_order` | OrderRequest | `preview_id, estimated_cost, warnings[]` (runs preview-time gates §4.2) |
| `place_order` | `preview_id` | `etrade_order_id, status` (runs all gates §4.2) |
| `get_order_status` | `etrade_order_id` | `status, filled_quantity, avg_price` |

**OrderRequest** (`etrade/models.py`): `symbol`, `order_action` (`BUY`/`SELL` — enum extensible to `SELL_SHORT`/options actions later), `quantity` (int > 0), `security_type` (`EQ` — enum extensible to `OPTN`), `order_type` (`MARKET`/`LIMIT`), `limit_price` (required iff LIMIT). Fields for riskier instruments exist now; §4.2 policy gates refuse them (T6).

### §5.3 Pipeline contracts — see §6.

### §5.4 Fixtures

Recorded real sandbox responses: `fixtures/etrade/<endpoint>.<key-params>.<YYYY-MM-DD>.json` (e.g. `get_quote.symbol-SPY.2026-07-15.json`). Record once via `scripts/record_fixture.py`; pydantic-validate at record time; **scrub `oauth_*` params, account numbers/keys before saving**. Replay tests + schema-drift tests are Phase 1 wall material. Sandbox data is canned — schema-drift tests re-run at Phase 6 prod cutover before any real order.

---

## §6 Decision pipeline

**Shape: undecided.** An explicit Phase 3 spike skims both reference repos before committing (ADR required):
- **TradingAgents** (TauricResearch) — analyst agents → bull/bear debate → trader → risk manager → portfolio manager.
- **AI Hedge Fund** (virattt) — investor-persona agents → risk manager → portfolio manager.

Evaluation criteria: simplicity (fewer prompts to audit), auditability of reasoning receipts, LLM turns per run (subscription-token cost). Bias per original plan: start simple, add debate rounds later once the base loop is trusted.

**Shape-agnostic contracts (frozen now, `pipeline/steps.py`):**
- `Decision` dataclass: `action` (`BUY`/`SELL`/`HOLD`), `symbol`, `quantity`, `confidence`, `reasoning_summary`, `signals` (list of dated evidence items — these become the T4 receipts).
- `PipelineStep` protocol: `run(context) -> context` — steps compose in a list; a role (analyst, aggregator, trader, risk-advisor) is just a step. No LangGraph or framework dependency.
- The pipeline's risk check is advisory (T1); output flows to the runner, which calls MCP tools; the server enforces.

**News/sentiment:** `NewsSource` protocol (`pipeline/news.py`): `headlines(symbol, since) -> list[NewsItem]`. v1 implementation uses Claude Code's built-in WebSearch during decision runs (no key, no rate-limit management). Interface is Finnhub-swappable if determinism/fixtures are wanted later.

---

## §7 Phases & walls

Each phase has entry criteria, deliverables, and a **named wall** — a pytest suite in `tests/wall/` (marker `wall`). CI runs each wall as its own job: informational (`continue-on-error`) while the phase is open, flipped to blocking at phase close with an ADR. The bootstrap **caps wall** (T5: refuse-missing-caps) is blocking from day one. Walls are never weakened to pass (safety-wall skill).

| Phase | Deliverables | Wall |
|---|---|---|
| **1 — E*Trade foundation** | OAuth 1.0a dance + renewal (2 hr idle timeout, nightly expiry — renewal approach is a Step-0 design question), six MCP tools against sandbox, fixtures + replay tests, hand-tested interactively via `.mcp.json` | **fixture wall**: replay + schema-drift tests for every endpoint |
| **2 — Safety layer** | Full §4 gate implementations, SQLite store, manual reset/kill CLI | **cap wall**: a test per §4.2 gate that attempts the violation and asserts the §4.1 refusal shape |
| **3 — Decision pipeline** | Spike ADR (shape choice), analyst/aggregator/trader steps, WebSearch news source, reasoning receipts flowing to trade_log | **pipeline wall**: given fixed inputs, Decision is schema-valid, receipts present, advisory risk check runs |
| **4 — Orchestration** | `runner/headless.py` in anger, launchd plist installed, full run: fetch state → pipeline → execute-within-caps → log → notify | **run wall**: end-to-end sandbox run executes ≤ caps and writes complete receipts |
| **5 — Observability** | ntfy events wired (§9), daily digest, status reports | (folds into run wall) |
| **6 — Pilot** | Prod cutover checklist (sandbox-prod skill), schema-drift re-run, fixed-window live run, SPY benchmark comparison, decision-gate report | — |

---

## §8 Configuration & secrets

### §8.1 `config/config.toml` (gitignored; `config.example.toml` committed)

```toml
config_version = 1          # bump on any caps/whitelist/policy change (ADR same commit)

[environment]
mode = "sandbox"            # "prod" only via sandbox-prod skill checklist

[capital]
pilot_amount_usd = 0.0      # the isolated, loss-tolerant amount; required > 0

[caps]                      # ALL REQUIRED, NO DEFAULTS (T5) — example file ships these commented out
# per_trade_pct = ...       # max % of pilot capital per order
# daily_trade_limit = ...   # max executed orders per day
# daily_loss_pct = ...      # daily loss % that trips the breaker

[whitelist]
tier1 = ["SPY","AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","JPM","V","UNH"]
tier2 = []                  # riskier names — future, via config_version bump + ADR
tier3 = []
enabled_tiers = ["tier1"]

[policy]
long_only = true
allowed_security_types = ["EQ"]

[store]
db_path = "trading.db"
```

### §8.2 Rules

- Caps and `pilot_amount_usd` load via pydantic models with **no defaults**; `load_config()` raises `ConfigError` naming every missing/invalid field; server startup dies on it (gate `caps-required`).
- Loosening risk (new tier entries, higher caps, new security types) is always an explicit act: `config_version` bump + ADR in the same commit.
- Secrets live in `.env` only (`ETRADE_CONSUMER_KEY`, `ETRADE_CONSUMER_SECRET`, `ETRADE_SANDBOX`, `NTFY_TOPIC`); access tokens persist to gitignored `tokens/`. Neither ever enters TOML, code, logs, or fixtures (T3).

---

## §9 Operations

- **Scheduling:** macOS launchd. Template in `launchd/`; rendered by `scripts/generate_plist.py` into `~/Library/LaunchAgents`. Plist sets `PATH` explicitly (launchd env is minimal), working directory, and stdout/stderr log files. `claude` availability + Max OAuth under launchd must be verified before Phase 4 trusts the schedule.
- **Cadence:** one decision run daily at market open (pilot). Each run: fetch state → pipeline → execute within caps → log → notify.
- **Notifications (ntfy.sh):** events = trade executed (ticker, side, qty, price, reasoning summary), breaker tripped, kill switch engaged/disengaged, daily digest (trades, P&L, caps remaining). Topic is a long random string from `.env`.
- **Logging:** JSONL via `logs.py` — `{ts, level, agent_id, message, data}` — to stdout + `logs/<agent>-<date>.jsonl`. The logger redacts values of known secret env vars before writing (T3).
- **Status:** `runner/status.py` writes a per-run JSON report (`status/`): run id, decisions, orders, refusals, duration, errors.
- **Headless invocation:** `runner/headless.py` — subprocess `claude -p` with `--allowedTools` whitelist, streaming tee to a log file, hard timeout kill. Max-subscription billing for headless use is an assumption to revisit periodically (the June-15 credit-pool split was paused).

---

## §10 Open questions

Tracked honestly; nothing below silently defers.

- [ ] **Exact cap numbers + pilot capital** — required before the server will start (T5); decide when funding the account.
- [ ] **Pipeline shape** — TradingAgents vs AI Hedge Fund; Phase 3 spike + ADR (§6).
- [ ] **OAuth token renewal approach** — interactive daily renew vs automated renew flow given the 2 hr idle / nightly expiry; Phase 1 Step-0 design question.
- [ ] **E*Trade sandbox key** — user action: request developer credentials; approval can take days–weeks. Phase 1 blocks on this.
- [ ] **Prod cutover checklist details** — drafted in Phase 6 planning; sandbox-prod skill holds the interim rules.
