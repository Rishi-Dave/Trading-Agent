# Phase 2 Report — Safety Layer

**Date:** 2026-07-10
**Status:** closed

## What shipped

Per SPEC §7 Phase 2's deliverable list:

1. **`store/db.py`** — SQLite WAL-mode `connect()` + forward-only migration
   runner against `store/schema.py::MIGRATIONS`. Idempotent on reconnect
   (won't re-apply an already-recorded version); creates parent directories;
   a fresh DB ships `kill_switch.engaged = 1` per SPEC §4.3.
2. **`store/state.py`** (new) — typed SQLite access: kill switch
   (`is_kill_engaged`/`set_kill_switch`), `caps_state`
   (`read_caps_state`/`increment_trades_executed`/`trip_breaker`/
   `reset_breaker`), `trade_log` receipts (`write_trade_log`,
   `record_executed_trade` — the latter added mid-phase, see below), and
   `today_utc()` (UTC calendar day, ADR-0003 point 2) as the single
   definition of "today" shared by the gate, both CLIs, and the remote
   listener.
3. **`server/safety.py::ConfiguredSafetyGate`** — every SPEC §4.2 gate
   implemented for real: `kill-switch`, `capital-ceiling`, `per-trade-cap`,
   `daily-trade-limit`, `loss-breaker`, `whitelist`, `policy-long-only`,
   `policy-security-type`, in the confirmed halts→legality→sizing order
   (ADR-0003 point 1). `preview-required` (Phase 1, T2) untouched.
   `check_preview`/`check_priced_preview`/`check_place` split (ADR-0003 point
   7) so cost-dependent gates run once, on the priced figure. Fail-closed on
   any exception (`server/CLAUDE.md`).
4. **The gate swap** — `server/app.py::create_app`: `PassthroughGate()` →
   `ConfiguredSafetyGate(config, client, state)`. `PassthroughGate` stays in
   the tree (Phase 1 artifact, ADR-0002) but is no longer reachable from
   `create_app`; behaviorally proven, not just asserted
   (`test_create_app_wires_configured_safety_gate_not_passthrough`).
5. **Manual reset/kill CLIs** — `scripts/reset_breaker.py`,
   `scripts/kill_switch.py`: typed confirmation + mandatory `--operator`,
   `--yes` to skip the prompt for scripted use, sandbox-only guard, logged +
   notified (ntfy; degrades to a logged warning if `NTFY_TOPIC` is unset).
6. **Remote triggering** — `scripts/remote_listener.py`: engage/disengage/
   reset-breaker fully operable from a phone via ntfy, on equal footing with
   the local CLIs (not a fallback), authenticated by a TOTP rotating code
   (`etrade_agent/totp.py`, RFC 6238, stdlib-only) — see "What drifted"
   below for why this isn't the static token the phase started with.
   `scripts/generate_totp_secret.py` provisions the shared secret.
7. **Cap wall** (`tests/wall/test_caps_wall.py`, extended in place — not a
   `phase2/` subdirectory, per the kickoff's framing of this as the direct
   continuation of the bootstrap caps wall). Committed red before gate logic
   existed, then implemented until green. One test per §4.2 gate's
   violation, plus two (now three) gate-ordering tests and three boundary
   tests added during the review fix pass. 35 wall tests total (7 Phase 1
   fixture wall + 28 caps wall), all blocking in CI (`safety-wall` job,
   unchanged — it already scoped to `wall and not phase1`, so it picked up
   every new test automatically).
8. **T4 receipts** — every successfully executed trade writes a `trade_log`
   row (`reasoning_summary`/`signals_json`/`caps_snapshot_json`, placeholders
   since Phase 3's pipeline doesn't exist yet). Extended during the review
   fix pass to also cover every refusal with a real `OrderRequest`
   (`check_preview`/`check_priced_preview`/`check_place`), matching SPEC
   §5.1's literal "one row per *attempted* order" — see below.
9. **ADR-0003** — batches every Step 0 decision (gate order, UTC day,
   live-positions loss-breaker P&L, CLI shape, remote-trigger scope) plus
   internal design decisions (gate dependency injection, the three-way check
   split, the state access layer, `realized_pnl`'s Phase 3 dependency) plus
   everything the whole-branch review changed. SPEC §4.2/§4.3/§8.2 amended
   in the same commits.

**Gates at close:** 169 unit tests, 35 wall tests (28 cap wall + 7 Phase 1
fixture wall) — all passing. `ruff check`, `ruff format --check`,
`mypy --strict` — all clean. One whole-branch review ran before close
(`superpowers:requesting-code-review`); one Critical and three Important
findings were fixed and verified (below); four Minor findings were also
fixed. The "deliberately break one gate, confirm the wall catches it, then
restore" teeth check (Phase 1's precedent) was re-run against `per-trade-cap`
and confirmed real.

## What drifted from spec (and why)

- **The remote-trigger authentication mechanism changed from a static token
  to TOTP mid-phase.** The initial implementation (matching the Step 0
  decision as originally scoped) authenticated `scripts/remote_listener.py`
  commands with a static, reusable secret token sent as the ntfy message
  body. The whole-branch review found this token was broadcast in cleartext
  over the same topic it authenticated — ntfy has no per-subscriber
  confidentiality and caches messages for replay, so the token became
  permanently visible and replayable after the first legitimate use,
  defeating the "requires the operator" property it existed to provide. This
  was a Critical finding, verified directly against the code before acting
  on it. Presented with remediation options, Rishi chose a uniform TOTP
  (RFC 6238) requirement across all three remote actions over
  minimizing friction on the fail-safe halt action alone. `etrade_agent/totp.py`
  implements it stdlib-only, verified against RFC 6238's own published test
  vectors. This is the single largest deviation from the phase's original
  scope, fully documented in ADR-0003 point 5 (including the rejected
  alternatives) and SPEC §4.3/§8.2.
- **Sizing gates (`capital-ceiling`, `per-trade-cap`) originally
  double-counted a SELL's own position as new exposure.** The same review
  found `_check_capital_ceiling`/`_check_per_trade_cap` summed live position
  market values and added the order's own `estimated_cost` regardless of
  direction — for a SELL, the position being sold is already counted in
  "current exposure," so adding its cost again could block a legitimate,
  risk-reducing exit (e.g. a fully-appreciated position that grew past the
  per-trade cap through price movement alone, with no way to sell it in one
  order). Verified by reproducing the failure with a concrete scenario, then
  fixed: both gates now skip entirely for SELL orders — `policy-long-only`'s
  held-quantity check is the real, sufficient bound for sells (SPEC §4.2
  doesn't distinguish direction in its table either; this reads as much a
  spec gap as a code bug).
- **`trade_log` receipts initially covered only successful placements,
  contradicting SPEC §5.1's own "one row per *attempted* order" text.** The
  kickoff prompt's Deliverable 6 language ("the mechanism for writing these
  receipts on a successful `place_order` is this phase's job") was read
  narrowly during initial implementation. The review caught the
  contradiction with §5.1's literal wording and the `refusal_gate` nullable
  column that exists specifically for refused attempts. Fixed by wiring
  `write_trade_log` into every refusal path that has a real `OrderRequest`
  to attach (`check_preview`, `check_priced_preview`, `check_place`) — the
  write path was already built and unit-tested for the refusal shape, making
  this cheaper than renegotiating the spec's wording. The one exception: the
  T2 `preview-required` refusal (unknown `preview_id`) has no `OrderRequest`
  at all, so it remains JSONL-only, unchanged from before.
- **Post-place state writes were originally unguarded.**
  `state.write_trade_log(...)` and `state.increment_trades_executed(...)`
  ran after the irreversible `client.place_from_binding()` call with no
  error handling — a state-write failure (plausible: the local CLIs, the
  remote listener, and the MCP server can all touch `trading.db`
  concurrently) would have left an executed-but-unlogged trade and an
  under-counted daily limit, a direct T4 violation. Fixed by combining both
  writes into one atomic `StateStore.record_executed_trade()` call, wrapped
  in a try/except that logs loudly (`level="error"`, full order/status
  details for manual backfill) on failure while still returning the real
  success to the caller — the order did execute; telling the caller
  otherwise could trigger an unsafe duplicate-place retry.
- **Minor cleanups from the same review**: `get_positions()` was being
  called up to three independent times per `check_place` pass; now fetched
  once and threaded through (also narrows the window before an eventual
  place call). `get_balances()` was declared on `PositionsProvider` but
  never used by any gate; removed (YAGNI) rather than kept as dead interface
  surface. `notify/ntfy.py`'s docstring overclaimed the ntfy topic itself as
  a security boundary; tightened to distinguish "kept private as hygiene"
  from "the actual authorization proof" (now TOTP, not topic secrecy).

## Open threads for Phase 3 (and beyond)

- **Pipeline shape spike** — SPEC §6, Phase 3's own deliverable. Every
  `reasoning_summary`/`signals_json` in `trade_log` is a placeholder until
  then.
- **`realized_pnl` stays at 0** — populating it automatically requires
  matching closed positions against cost basis across trades, which depends
  on Phase 3's pipeline position-tracking. The loss-breaker is fully
  safety-functional on live unrealized P&L alone in the interim (ADR-0003
  points 3, 9) — not a silent gap, but worth wiring once the pipeline
  exists.
- **`renew_tokens()` still isn't wired into any runtime call site**
  (carried forward from Phase 1's open thread, PHASE1-REPORT.md — untouched
  this phase, since Phase 2's scope was the safety layer, not OAuth).
- **Malformed tool input can still surface a raw `ToolError`** (also
  carried forward from Phase 1 — `preview_order`'s FastMCP wrapper
  constructs enums from raw strings before the gate ever runs; a bad value
  raises uncaught. Low real-world risk with a well-formed orchestrator, but
  worth tightening before an unattended Phase 4 runner is trusted with
  malformed input from a model).
- **`scripts/remote_listener.py` is not itself scheduled** — no launchd
  unit yet; running it is a manual, opt-in step through at least Phase 4/5,
  consistent with SPEC §7's phase boundaries. This phase only builds the
  mechanism.
- **Phone control isn't functional yet in practice** — `NTFY_TOPIC` and
  `NTFY_COMMAND_SECRET` both need real values in `.env`, and the secret
  needs one-time setup in an authenticator app
  (`scripts/generate_totp_secret.py`) before `scripts/remote_listener.py`
  does anything meaningful. `NTFY_TOPIC` has been unset since Phase 1 — this
  phase doesn't block on it being set (per the kickoff's framing), but
  nothing here works end-to-end until Rishi provisions both.
- **Real cap numbers + pilot capital** — still open per SPEC §10, decided
  when Rishi funds the account. This phase built the full mechanism against
  synthetic test values (`tests/conftest.py::VALID_CONFIG_TOML`), same as
  Phase 1; it does not require or wait on the real numbers. The throwaway
  `config/config.toml` from Phase 1 must still be replaced entirely before
  any real trading.
- **`positions_cache` (SPEC §5.1) remains unpopulated/unused** — deliberate:
  every safety-relevant read goes through live `get_positions()` (ADR-0003
  point 3), and no code path writes to the cache table yet. Whether/when to
  wire it up (e.g. for a fast status display) is undecided and not blocking.
- **Sandbox canned-data limitations** (Phase 1's note, still relevant):
  fixed demo symbols/prices/order ids in sandbox responses mean the safety
  gates have never been exercised against realistic, varied live data —
  only synthetic `FakeMarket`/`RaisingMarket` test doubles and the
  deliberately-controlled hand-test in this phase's verification. Schema-drift
  tests must re-run against production before Phase 6's first real order,
  per the etrade-fixtures skill.
