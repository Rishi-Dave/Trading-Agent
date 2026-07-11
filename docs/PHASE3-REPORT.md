# Phase 3 Report — Decision Pipeline

**Date:** 2026-07-11
**Status:** closed

## What shipped

Per SPEC §7 Phase 3's deliverable list:

1. **Spike ADR (ADR-0004)** — the four kickoff-mandated Step 0 decisions,
   confirmed with Rishi before any step logic was written: shape
   (multi-analyst, no debate — news/technical/fundamental analysts →
   deterministic aggregator → trader → advisory risk, deferring debate rounds
   and investor personas per SPEC §6's "start simple" bias), LLM/news access
   (an injected `LLMClient`/`MarketDataSource` seam, concrete `claude -p`
   adapter built this phase but wired into a live pipeline only by Phase 4),
   advisory risk placement (both a deterministic caps/whitelist mirror and a
   qualitative LLM step, T1: neither can block), and the `Signal.detail`
   evidence schema (frozen `Signal`/`Decision` dataclasses untouched). Also
   batched: pipeline wall placement (`tests/wall/phase3/`, mirroring the
   Phase 1 precedent) and the `realized_pnl` deferral (still Phase-4-coupled,
   not Phase-3-coupled — see below).
2. **`pipeline/steps.py`** — the frozen `Decision`/`PipelineStep` contracts
   (already scaffolded pre-phase) plus every concrete step: `NewsAnalystStep`,
   `TechnicalAnalystStep`, `FundamentalAnalystStep` (share `_query_llm_signal`,
   one JSON-object-in/`Signal`-out contract — analysts differ by prompt/
   domain, not by code path), `AggregatorStep` (deterministic, zero LLM
   turns), `TraderStep`, `CapsMirrorRiskStep` and `RiskAdvisorStep` (both
   advisory-only — append to `context.notes`, never touch
   `context.decisions`), `run_pipeline` (plain `for step in steps` loop — no
   framework dependency), and `signals_to_json` (the canonical
   `trade_log.signals_json` serialization).
3. **`pipeline/llm.py`** (new) — `LLMClient` Protocol, the one seam every
   reasoning step depends on. **`pipeline/market.py`** (new) —
   `MarketDataSource` Protocol, read-only, satisfied structurally by
   `EtradeClient` with no changes to `etrade/client.py` (same trick as Phase
   2's `PositionsProvider`).
4. **`pipeline/news.py`** — added `WebSearchNewsSource(llm: LLMClient)`, the
   v1 `NewsSource` implementation: prompts the injected `LLMClient` with
   `allowed_tools=["WebSearch"]`, parses the response into `NewsItem`s,
   drops anything malformed rather than raising. `NewsSource` stays
   Finnhub-swappable — nothing about the protocol changed.
5. **`runner/llm_client.py`** (new) — `ClaudeLLMClient`, the concrete
   `claude -p`-backed `LLMClient`, wrapping the existing
   `runner/headless.py::claude_query`. Required extending `claude_query`
   itself with an `allowed_tools` parameter (`--allowedTools` pass-through)
   since it previously had no tool-whitelisting support at all — a small,
   backward-compatible addition (default `None`, existing behavior
   unchanged), covered by new `tests/runner/test_headless.py`. `runner/` is
   the only module that shells out to `claude`; `pipeline/` depends on the
   `LLMClient` Protocol only and never imports `runner/` (module map, SPEC
   §3.1) — confirmed by grep before commit (spec-compliance skill, T1).
6. **Reasoning receipts flowing to `trade_log` for real** — Phase 2's
   placeholder (`_NO_PIPELINE_REASONING`, `signals_json="[]"`, hardcoded at
   three call sites in `server/tools.py`) is now the *default*, not the only
   path. `StoredPreview` (`server/preview_store.py`) gained
   `reasoning_summary`/`signals_json` fields, bound at `preview_order` time
   (T2-aligned — no new place-time parameter; `place_order` inherits them
   from the binding). `preview_order` gained optional
   `reasoning_summary`/`signals_json` parameters, threaded through to every
   refusal receipt and the success-path `StoredPreview`; `place_order` reads
   `entry.reasoning_summary`/`entry.signals_json` for both its refusal
   receipt and `record_executed_trade` call, replacing the two literals that
   used to sit there unconditionally. The MCP `_preview_order` wrapper
   exposes both as optional tool parameters. The placeholder constant itself
   moved to `preview_store.py` (the natural owner of the binding it
   defaults) and its text changed from "no decision pipeline yet (Phase 3)"
   to "no pipeline reasoning supplied with this call" — accurate now that a
   pipeline exists but a given call may simply not have used one.
7. **Pipeline wall** (`tests/wall/phase3/`, `phase3` marker) — SPEC §7's
   three-part Phase 3 wall text, one assertion group per clause: (a)
   schema-valid `Decision` from fixed inputs, (b) receipts present *and
   genuine* (non-placeholder `reasoning_summary`, dated non-empty `signals`,
   `signals_to_json` round-trips), (c) both advisory risk steps ran and only
   annotated (`context.decisions` provably unchanged — identity-compared
   before/after), plus a fourth assertion beyond SPEC's literal wording that
   the kickoff prompt's own "receipt seam" deliverable needed: a Decision's
   real reasoning provably reaches a `trade_log` row through
   `preview_order`→`place_order`, not just the placeholder. Deterministic —
   fake `LLMClient`/`NewsSource`/`MarketDataSource` fed from recorded
   responses under `fixtures/pipeline/` (new dir, six files, ADR-0004 point
   5), no live network or model call in the wall. `phase3` marker mirrors the
   `phase1` precedent exactly (own `conftest.py`, own CI job); `ci.yml`'s
   `safety-wall` job scope extended from `wall and not phase1` to
   `wall and not phase1 and not phase3` in the *same* commit that introduced
   the marker, so the day-one-blocking caps wall was never at risk of
   picking up a still-red phase3 test mid-session.
8. **Regular unit tests** — `tests/pipeline/test_steps.py` (27 tests, one
   step in isolation per test via hand-built `PipelineContext`s — malformed-
   response rejection, HOLD/quantity-zero handling, the advisory steps'
   never-mutates-decisions guarantee, market-data-exception degrade-to-flag),
   `tests/pipeline/test_news.py` (8 tests, `WebSearchNewsSource` parser
   edge cases), `tests/runner/test_llm_client.py` +
   `tests/runner/test_headless.py` (6 tests, fake-subprocess — no live
   `claude` call), `tests/server/test_preview_store.py` /
   `tests/server/test_tools.py` extended (8 new tests) for the reasoning
   passthrough specifically, distinct from the wall's end-to-end proof.

**Gates at close:** 226 unit tests (up from 169 at Phase 2 close, +9 from the
whole-branch review's fix pass below), 39 wall tests (28 caps wall + 7 Phase
1 fixture wall + 4 Phase 3 pipeline wall) — all passing. `ruff check`,
`ruff format --check`, `mypy --strict` — all clean across 28 source files.
spec-compliance checklist walked before committing (T1 import-boundary grep,
T2 single-caller grep, T3 fixture content review, T4 receipt-field trace, T5
no-new-defaults check, T6 untouched-gates confirmation) — clean, but see
below for a real bug the checklist's grep-only pass did *not* catch, that
the subsequent whole-branch review did. One whole-branch review ran before
close (`superpowers:requesting-code-review`, per CLAUDE.md's Review Policy),
independently verifying every gate command itself rather than trusting this
report's draft; one Critical and two Important findings came back, all
addressed below.

## What drifted from spec (and why)

- **The pipeline wall's own fake caught a real substring-matching bug during
  authoring, not a design defect.** The wall's `FakeLLMClient` originally
  dispatched canned responses by checking `"[tag]" in prompt`. The
  `TraderStep` prompt embeds each signal's evidence as
  `f"- [{s.source}] {s.summary}"` — so the trader's own prompt, which starts
  with `[trader]`, also *contains* the substring `[news-analyst]` in its
  evidence body. The fake matched that substring first (ifs checked
  news-analyst before trader) and handed the trader a news-analyst-shaped
  response, which failed to parse as a decision, leaving
  `context.decisions` empty and the wall red for the right reason on its
  first real run. Fixed by switching the fake's dispatch to `startswith`
  (every template is prefixed with its own tag, so this is exact) — a
  one-line fix to the test double, not to `pipeline/steps.py`. Left in this
  report because it's exactly the kind of thing the wall exists to catch,
  even against a fake rather than a live model.
- **`Signal.detail["symbol"]` carries the grouping key, not a `context`-level
  field.** SPEC §6 froze `Signal(source, as_of, summary, detail)` with no
  `symbol` field of its own. `AggregatorStep`/`TraderStep` need to group
  signals by symbol, so each analyst step stamps `detail["symbol"]` as
  factual metadata into `_query_llm_signal`'s `extra_detail`, and the
  grouping helper reads it back out. This is squarely inside "detail
  carries source-specific structure" (ADR-0004 point 4), not a deviation
  from it, but is worth naming explicitly since `symbol` doing double duty
  as both grouping key and evidence content is a convention future step
  authors need to keep, not something the type system enforces.
- **Critical, whole-branch-review-caught: `extra_detail`'s merge order was
  inverted, so the LLM's own `detail` keys silently won over code-supplied
  ground truth — the opposite of the intended and originally-documented
  guarantee.** `_query_llm_signal` (`pipeline/steps.py`) originally built
  `detail = {**(extra_detail or {}), **model_detail}` — Python's `{**a, **b}`
  has `b` win on key collision, so a model response containing its own
  `"symbol"` key (hallucinated, or for `FundamentalAnalystStep`/
  `WebSearchNewsSource` — both WebSearch-backed — plausibly prompt-injected
  via scraped content) silently overrode the real, code-supplied symbol.
  Since `_signals_by_symbol` groups purely off `detail["symbol"]`, this could
  reroute a piece of evidence into a different symbol's aggregate/Decision —
  a genuine T4 integrity gap (the "why" reconstructed from `trade_log` could
  attribute evidence to the wrong symbol), though not a T1/T2 gap: the
  `Decision.symbol`/`OrderRequest.symbol` a trade actually executes under
  still comes from the code-controlled loop variable in `TraderStep.run`,
  never from model output, so this could not have hijacked *which* symbol
  traded. No fixture or test exercised a colliding key, so both the wall and
  the unit suite were blind to it until the review's manual trace. Fixed by
  swapping the merge to `{**model_detail, **(extra_detail or {})}` (code
  wins), with a regression test
  (`test_news_analyst_code_supplied_symbol_wins_over_hallucinated_model_detail`).
- **Important, whole-branch-review-caught: no step degraded per-symbol on a
  transport failure.** `CapsMirrorRiskStep` was the only step with a
  try/except around its external call; every LLM-calling step
  (`NewsAnalystStep`, `TechnicalAnalystStep`, `FundamentalAnalystStep`,
  `TraderStep`, `RiskAdvisorStep`) let an `LLMClient`/`NewsSource`/
  `MarketDataSource` exception propagate straight out of `run()`, so one
  flaky `claude -p` call (timeout, nonzero exit, a WebSearch hiccup) for one
  symbol would abort `run_pipeline` for every remaining symbol and step —
  fail-safe in effect (no `Decision` means no order attempt) but silently
  zeroing an entire day's run over one bad call. Fixed by wrapping each
  step's per-symbol/per-decision body in `try/except Exception`, logging via
  a new shared `_log_step_skip` helper (`pipeline/steps.py`, using the
  `logs` module `pipeline/` is permitted to import per the module map) and
  continuing to the next symbol rather than raising. `TraderStep` also now
  logs (not just silently `continue`s) when a response parses as JSON but
  isn't a usable decision, so `trade_log`'s silence on a symbol is no longer
  indistinguishable between "no evidence to trade on" and "the trader's
  response didn't parse" — a Minor finding from the same review, folded into
  this fix since it touched the same code. Nine new tests cover both the
  degrade-on-exception path (one per step) and the new log line.
- **`claude_query` needed a small signature extension mid-phase.** The
  kickoff prompt anticipated the pipeline needing WebSearch access but
  `runner/headless.py::claude_query` (Phase 1) had no tool-whitelisting
  parameter at all — it always ran with zero tool access. Adding
  `allowed_tools: list[str] | None = None` (mapping to `--allowedTools`) was
  the minimal change; `None` preserves every existing call's behavior
  exactly, so this isn't a breaking change to Phase 1 surface, just filling
  in a gap Phase 1 had no reason to hit yet (nothing needed tool access
  before this phase).

## Open threads for Phase 4 (and beyond)

- **Advisory risk output (`context.notes["risk_advisories"]`/
  `["risk_advisory_llm"]`) has no path to durable storage** (Important,
  whole-branch-review finding, deliberately not fixed this phase). Both
  `CapsMirrorRiskStep` and `RiskAdvisorStep` compute genuine advisory reads
  — including potentially a `"concern_level": "high"` flag — but
  `PipelineContext.notes` is purely in-memory and nothing threads it into
  `trade_log` (only `Decision.reasoning_summary`/`.signals` are, via the
  `preview_order`/`place_order` wiring). If a risk step flags a trade
  high-concern and the trade still executes, that flag currently evaporates
  at the end of the pipeline run with no record anywhere. Not a SPEC/ADR
  violation — the Phase 3 wall only requires the steps "ran" — and the
  reviewer's own recommendation was to treat this as Phase 4 runner-design
  scope (the "log" step of fetch→pipeline→execute→log→notify is exactly
  where `context.notes` would get persisted) rather than a Phase 3 blocker.
  Flagged explicitly here so it doesn't silently stay unaddressed.
- **`runner/headless.py` has no decision-run loop yet** — this phase built
  the pipeline and the `ClaudeLLMClient`/`WebSearchNewsSource` adapters that
  *can* be wired into a live run, but nothing yet constructs a live
  `PipelineContext`, runs it against `EtradeClient`-as-`MarketDataSource`,
  maps a `Decision` to an `OrderRequest`, or calls
  `preview_order`/`place_order` with real reasoning outside a test. That
  whole loop (fetch state → run pipeline → execute within caps → log →
  notify) is SPEC §7 Phase 4's own deliverable, unchanged.
- **`realized_pnl` still stays at 0** — carried forward a second time from
  Phase 2 (ADR-0003 point 9, `PHASE2-REPORT.md`). The pipeline *proposes*
  Decisions; it doesn't execute fills or track cost basis, so it has
  nothing to feed automatic population with. That dependency is coupled to
  Phase 4's execution loop, not to this phase's proposal logic (ADR-0004
  point 6) — still tracked explicitly, not silently dropped from the list a
  second time.
- **launchd plist not installed; `claude`/Max auth under launchd unverified**
  — SPEC §9's operational checklist, unchanged from Phase 1/2, still Phase
  4's to close.
- **No live end-to-end run has happened.** Every pipeline/receipt-seam claim
  in this report is proven against fakes (unit tests) or fixture-recorded
  responses (the wall) — genuinely deterministic and fast, but the first
  time a real `claude -p` WebSearch call, a real E*Trade sandbox quote, and
  a real trader Decision flow through the whole chain together is Phase 4's
  first run, not this phase's.
- **`ClaudeLLMClient`/`WebSearchNewsSource` are exercised only by fakes so
  far** — `tests/runner/test_llm_client.py` monkeypatches `claude_query`
  itself; nothing in this phase's test suite shells out to a real `claude`
  process. Phase 4 (or an interactive hand-test, per the sandbox-prod-
  adjacent discipline Phase 1 used for its six MCP tools) is where that
  first live call happens.
- **`FundamentalAnalystStep`/`WebSearchNewsSource`'s WebSearch calls are
  unbounded in this phase's design** — no rate-limiting, caching, or
  cost-tracking around how many WebSearch-enabled `claude -p` calls one
  decision run makes (three analyst steps + N symbols, today N=1 in the
  wall). At the pilot's once-daily cadence this is a non-issue, but worth
  naming before Phase 4 scales symbol count or run frequency.
- **Real cap numbers + pilot capital** — still open per SPEC §10, unchanged
  a third time; this phase, like Phases 1–2, builds and tests entirely
  against `tests/conftest.py::VALID_CONFIG_TOML`.
- **Every other Phase 2 open thread not mentioned above** (`renew_tokens()`
  unwired, malformed-tool-input `ToolError` leakage, `remote_listener.py`
  unscheduled, phone control needing `.env` provisioning,
  `positions_cache` unpopulated, sandbox canned-data limitations) is
  untouched by this phase's scope and remains exactly as `PHASE2-REPORT.md`
  left it — not re-litigated here to avoid the list silently drifting from
  what's actually still true.
