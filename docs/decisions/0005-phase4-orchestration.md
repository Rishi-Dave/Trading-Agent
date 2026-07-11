# 0005 — Phase 4 orchestration: runner shape, OAuth renewal, realized_pnl deferral, advisory-note durability

**Date:** 2026-07-11
**Status:** accepted

## Context

Phase 4 (SPEC §7) makes the decision loop real and unattended: fetch state -> pipeline
-> execute-within-caps -> log -> notify, on a launchd schedule with no human present.
Phases 1-3 built every piece in isolation (OAuth client, six gated MCP tools +
`ConfiguredSafetyGate`, the multi-analyst pipeline with injected `LLMClient`/
`NewsSource`/`MarketDataSource` seams and real T4 receipts) but nothing wired them
into a live run — no `Decision`-to-`OrderRequest` adapter, no `runner/__main__.py`, no
shared runtime-construction path, and `scripts/generate_plist.py`/`runner/status.py`
were stubs (`status.py` stays Phase 5's). The kickoff prompt required four judgment
calls be proposed, confirmed with Rishi, and ADR'd before writing loop logic —
batched here per the adr-writing skill, alongside the implementation-time decisions
that followed directly from them.

## Decision

**1. Runner shape: direct in-process (SPEC §3.1 Step 0 #2).** `runner/decision_run.py`
imports `server.tools`/`server.app`/`pipeline.steps` directly and calls the plain
`preview_order`/`place_order` functions in the same process as the pipeline — not an
MCP client over stdio. To guarantee the runner and the interactive MCP server
(`server/app.py::create_app`) build the *same* gate/client/store from *one* code path
(T1: no second, divergently-constructed enforcement setup), `create_app`'s object
construction is extracted into `build_runtime(config_path, tokens_dir) -> Runtime`
(a frozen dataclass: `config, client, gate, store, state, run_id`); `create_app`
becomes a thin `FastMCP` wrapper around it. Chosen over an MCP-client shape because it
is simpler (no new client-library dependency for one daily run) and because
`ConfiguredSafetyGate` still runs unconditionally either way — the process-boundary
purity an MCP-client shape preserves isn't worth a second `EtradeClient`/OAuth session
for a once-daily run. **SPEC §3.1's module-import table is amended**: `runner/` may
now import `server`, `etrade`, `store`, and `pipeline` (previously `config, logs,
notify` only). The "nothing imports `runner/`" rule is unchanged in the other
direction — `pipeline/` still depends only on the `LLMClient`/`NewsSource`/
`MarketDataSource` Protocols, never on `runner/`.

**2. OAuth: automate the possible, keep one human step (SPEC §10, amends ADR-0002
point 1).** E*Trade access tokens hard-expire at midnight ET; the oob authorization
dance structurally requires a human to read a verifier code from a browser — nothing
non-interactive can survive that expiry (ADR-0002 point 1, verified live). What *can*
be automated is idle-timeout recovery (E*Trade's 2 hr inactivity window): `renew_tokens()`
(built in Phase 1, zero callers since) is now wired into `build_runtime`, called
unconditionally, right after `oauth.load_tokens`, via a `_best_effort_renew` helper.
A renewal failure — including the *expected* case of a token dead past midnight, which
`renew_tokens` cannot survive — is caught and logged, never fatal: `build_runtime`
proceeds with the original tokens, and the very next call
(`EtradeClient.connect`/`signed_session` use) is the real liveness check that fails
closed on a token that's genuinely dead. Both `create_app` and the runner benefit from
the same renewal attempt, since both go through `build_runtime`. The residual ~30s
`scripts/oauth_login.py` dance stays a human, once-daily step — this is the maximal
automation E*Trade's OAuth 1.0a flow actually permits, not a partial fix. The runner's
own entrypoint (`runner/__main__.py::main`) additionally fails clean on a startup
error: it catches `ConfigError`/`ServerStartupError`, sends an ntfy alert (naming
`oauth_login.py` specifically when the failure is token-related), and exits nonzero —
never a raw traceback into launchd's stderr log. This resolves SPEC §10's "OAuth token
renewal approach" open question for good: manual daily dance + automated idle recovery
+ clean-fail preflight.

**3. `realized_pnl`: re-deferred, a third time, explicitly.** Sandbox `place_from_binding`
responses are canned (`status="OPEN"`, `filled_quantity=0` — E*Trade's place endpoint
never reports fills; `get_order_status` is documented as the source of truth, but
sandbox fills are themselves canned/unreliable per the etrade-fixtures skill's sandbox
caveat). There is no real fill/cost-basis data this phase's execution loop can feed
automatic `realized_pnl` population with, and lot-level position tracking (matching
closed positions against cost basis across trades) is a distinct subsystem, not
orchestration — building it here would mean building position-tracking-phase-shaped
code inside this phase's scope, the same reasoning ADR-0003 point 9 and ADR-0004 point
6 used for Phases 2 and 3. The loss-breaker continues to function on **live**
unrealized P&L alone (ADR-0003 point 3), unaffected — `realized_pnl` stays 0 at every
`caps_state` read through the end of Phase 4. Deferred to a dedicated position-tracking
phase, or Phase 6 (real prod fills), whichever comes first.

**4. Advisory-risk notes: durable JSONL now, via the runner's "log" step.** Phase 3's
review flagged that `context.notes["risk_advisories"]`/`["risk_advisory_llm"]` had no
path to durable storage — a high-concern flag on an executed trade could evaporate at
the end of the pipeline run with no record anywhere (`PHASE3-REPORT.md`). Resolved
here: `runner/decision_run.py::run_decision`'s "log" step
(`_log_advisory_notes`) writes the run's full `context.notes` dict to JSONL via the
existing `logs.log(..., log_dir=...)` mechanism, keyed by `run_id`, under a distinct
`etrade-runner` agent id (`logs/etrade-runner-<date>.jsonl`) — no new logging
mechanism, reusing Phase 2's `logs.py`. Richer per-run status JSON carrying the same
notes is still Phase 5's `runner/status.py` to build; this is the minimal durable
receipt, not the final observability surface.

**5. `Decision` -> `OrderRequest` mapping (`runner/decision_run.py::_decision_to_order`).**
HOLD actions and non-positive quantities map to `None` (no order attempt, logged at
warning) — this is exactly the standing-warning surface the kickoff prompt flagged: a
mapping bug here would hide a silent order. A malformed `Decision` (e.g. an empty
symbol) also maps to `None` via a caught `pydantic.ValidationError` around
`OrderRequest` construction, rather than raising and aborting the run's remaining
decisions. BUY/SELL map to a `MARKET`/`EQ` order — `Decision` carries no price field,
so LIMIT orders are out of scope for the pipeline-driven path (an operator using the
MCP tools directly can still place LIMIT orders; nothing about that path changed).
**Whitelist membership is deliberately NOT checked in this mapping function** — a
non-whitelisted symbol becomes a real order attempt that `preview_order`'s `whitelist`
gate refuses, producing a genuine `trade_log` refusal receipt. Pre-filtering it here
would be a second, silent enforcement path outside `server/safety.py` (T1) and would
mean a non-whitelisted Decision leaves no trace at all — the gate's refusal receipt is
the correct non-silent outcome.

**6. Notification seam: an injectable `NotifyFn`, never fatal.** `execute_decisions`/
`run_decision` take `notify: Callable[[str, str], None]` rather than calling
`notify.ntfy.send` directly, so the run wall (and unit tests) can inject a collector
with no live network. The production implementation
(`runner/decision_run.py::build_notify`) wraps `notify.ntfy.send`: a missing
`NTFY_TOPIC` degrades to a logged warning (not a crash — matches the existing pattern
in `scripts/kill_switch.py`/`reset_breaker.py`), and a send failure is caught and
logged rather than propagated — by the time `notify` is called the trade or refusal has
already happened (or the run has already been skipped); a missed notification is a
monitoring gap, never a reason to fail an otherwise-successful run.

## Alternatives

- **Point 1 — MCP client over stdio**: preserves the SPEC §3 architecture diagram's
  process boundary literally, but requires a second `EtradeClient`/OAuth session
  (the pipeline still needs a `MarketDataSource` in-process either way) and a new
  MCP-client-library dependency for one run/day — rejected as unjustified complexity;
  T1 is preserved either way since `ConfiguredSafetyGate` runs regardless of transport.
- **Point 2 — build a fully unattended OAuth flow (scripting the browser
  login/storing E*Trade web credentials)**: rejected again, explicitly, on the same
  grounds ADR-0002 point 1 rejected it originally — E*Trade's oob flow structurally
  requires a human browser step, and automating around that means storing web login
  credentials, a new secret-exposure surface for marginal gain (it still couldn't
  survive the midnight expiry without a verifier code).
- **Point 2 — no renewal automation at all (status quo)**: simpler, but leaves
  `renew_tokens()` permanently dead code and means an unattended run fails at the
  first API call any time the token has merely gone idle within the same day (a
  realistic gap given the pilot's market-open cadence can trail the morning login by
  hours) — rejected as leaving a cheap, safe automation on the table.
- **Point 3 — wire fill-polling + lot-level cost-basis matching now**: rejected;
  sandbox data can't validate it meaningfully (canned OPEN/filled-0 responses), and it
  expands this phase's scope well beyond orchestration into position-tracking.
- **Point 4 — defer advisory-note durability to Phase 5's `status.py`**: rejected;
  would carry the same open thread forward a fourth time, with real risk (a
  high-concern flag on an executed trade with no record) for the sake of leaner
  Phase 4 scope — the fix is a few lines reusing an existing mechanism (`logs.log`),
  not new infrastructure.
- **Point 5 — filter non-whitelisted symbols before ever calling `preview_order`**:
  rejected; it would create a second enforcement path outside `server/safety.py` (T1)
  and would leave a non-whitelisted Decision with no trace anywhere, unlike a real
  gate refusal, which lands a `trade_log` row.
- **Point 6 — call `notify.ntfy.send` directly, no seam**: rejected; would make the
  run wall's "receipts complete" proof require live network for notifications, and
  would let a notification-service outage fail an otherwise-successful trade run.

## Consequences

- `build_runtime`/`Runtime` (`server/app.py`) is now the single object-construction
  path for both the interactive MCP server and the Phase-4 runner — any future
  safety-gated caller should build on this, not construct its own copy of
  `ConfiguredSafetyGate`/`EtradeClient`/`StateStore`.
- SPEC §3.1's module-import table changes: `runner/` may import `server`, `etrade`,
  `store`, `pipeline` (was `config, logs, notify` only). SPEC §10's OAuth open
  question is resolved (not removed from history — noted as resolved).
- `renew_tokens()` (ADR-0002, Phase 1) has its first caller; `realized_pnl` remains 0
  through Phase 4's close, tracked a fourth time (via this ADR and `PHASE4-REPORT.md`)
  as still open, not silently dropped.
- The run wall (`tests/wall/phase4/`, `phase4` marker) mirrors the `phase1`/`phase3`
  precedent exactly: own `conftest.py`, own CI job (`continue-on-error: true` while
  Phase 4 is open), and `ci.yml`'s `safety-wall` selector gains `and not phase4` in the
  same commit that introduces the marker — the day-one-blocking caps wall was never at
  risk of picking up a still-red phase4 test mid-session, the same discipline Phase 3
  used.
- `scripts/generate_plist.py` renders the plist and prints (never runs)
  `launchctl load`/`unload` — scheduling stays a deliberate, separate operator action,
  consistent with the reset/kill CLIs' typed-confirmation discipline elsewhere in this
  repo.
