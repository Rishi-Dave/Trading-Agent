# Phase 4 Report — Orchestration

**Date:** 2026-07-11
**Status:** closed (wall open — informational until phase-close ADR flips it, per precedent)

## What shipped

Per SPEC §7 Phase 4's deliverable list:

1. **Spike ADR (ADR-0005)** — the four kickoff-mandated Step 0 decisions, confirmed
   with Rishi before any loop logic was written: runner shape (direct in-process, via
   a new shared `server.app.build_runtime()`/`Runtime` construction path — not an MCP
   client over stdio), OAuth renewal (automate the possible: `renew_tokens()` wired in
   as best-effort idle-timeout recovery, the midnight-expiry human dance stays,
   resolving SPEC §10), `realized_pnl` (re-deferred a third time — no real fills to
   compute from), and advisory-risk-note durability (resolved now, via JSONL in the
   runner's "log" step — closing a Phase 3 open thread). Also batched: the
   `Decision`→`OrderRequest` mapping rules and the injectable-notify design.
2. **`server/app.py::build_runtime`/`Runtime`** — `create_app`'s object construction
   (config, client, gate, store, state, run_id) extracted into a reusable factory, so
   the interactive MCP server and the runner build the *same* `ConfiguredSafetyGate`
   from *one* code path (T1). Also wires `oauth.renew_tokens()` — built in Phase 1,
   zero callers until now — as an unconditional best-effort call right after token
   load; a failure (including the *expected* midnight-expiry case) is caught, logged,
   and non-fatal, since the very next call (`EtradeClient.connect`) is the real
   liveness check.
3. **`runner/decision_run.py`** (new) — `_decision_to_order` (the HOLD/malformed/
   non-positive-quantity → no-order-attempt mapping; whitelist deliberately not
   pre-filtered here, so a non-whitelisted symbol still reaches the gate and gets a
   real refusal receipt), `execute_decisions` (drives real `preview_order`/
   `place_order` per decision — the unit the run wall exercises directly against the
   real gate), `run_decision` (the full fetch→pipeline→execute→log→notify loop: a
   kill-switch preflight optimization, the canonical Phase 3 pipeline assembly with
   live-or-fake seams, `_log_advisory_notes` as the durable-JSONL "log" step, and a
   run-summary notification), and `build_notify` (the production `NotifyFn`: wraps
   `notify.ntfy.send`, never fatal on a missing topic or a send failure).
4. **`runner/__main__.py`** (new) — `python -m etrade_agent.runner`, the plist's
   `ProgramArguments` target (didn't exist before this phase). Checks `claude`
   availability before constructing a live `ClaudeLLMClient` (skipped when a test
   injects one), calls `build_runtime`, classifies startup failures (`ConfigError`,
   `ServerStartupError` — naming `oauth_login.py` specifically for token failures),
   and wraps `run_decision` in a catch-all so an unexpected mid-run exception becomes
   an ntfy alert + nonzero exit rather than a raw traceback landing in launchd's
   `StandardErrorPath` (ADR-0002 point 9's carried-forward concern).
5. **`scripts/generate_plist.py::main`** — implemented for real: renders
   `launchd/com.rishi.trading-agent.decision-run.plist.template`, fills all five
   placeholders (`LABEL`/`WORKDIR`/`PATH`/`HOUR`/`MINUTE` — `PATH` baked in from the
   rendering shell's own environment, the fix for launchd's minimal runtime env per
   SPEC §9), writes to `~/Library/LaunchAgents/`, and prints (never runs) the
   `launchctl load`/`unload` commands.
6. **Notifications** — trade-executed (with reasoning summary), decision-run-skipped
   (kill switch), decision-run-complete (summary counts), and every failure path in
   `__main__.py` — all via the existing `notify/ntfy.py` (Phase 2), no new mechanism,
   reached through the injectable `NotifyFn` seam so the run wall proves receipt
   completeness without live network.
7. **Run wall** (`tests/wall/phase4/`, `phase4` marker) — SPEC §7's "end-to-end
   sandbox run executes ≤ caps and writes complete receipts," seven tests: complete
   T4 receipts through a full fake-seam pipeline run, `daily-trade-limit` stopping
   execution at exactly the configured limit, an oversized order refused by
   `per-trade-cap`, a HOLD producing zero order attempts, a non-whitelisted symbol
   refused (not silently dropped — a real `trade_log` refusal row), a kill-switch-
   engaged run refusing at `place` with zero executions, and advisory notes landing in
   durable JSONL. Unlike the Phase 3 wall's `_AllowGate`, every scenario here drives
   the loop against the **real** `ConfiguredSafetyGate` (fake `LLMClient`/
   `NewsSource`/`EtradeClient` only) — "executes ≤ caps" is proven against actual §4.2
   gate logic, not a stand-in. `phase4` marker mirrors the `phase1`/`phase3` precedent
   exactly (own `conftest.py`, own CI job); `ci.yml`'s `safety-wall` job scope extended
   from `wall and not phase1 and not phase3` to `... and not phase4` in the *same*
   commit that introduced the marker.
8. **Regular unit tests** — `tests/server/test_app.py` (+3 for `build_runtime`/
   `Runtime`/renewal, existing tests updated with one hermeticity-preserving
   monkeypatch each so the new unconditional renewal attempt never makes a live
   network call in tests), `tests/runner/test_decision_run.py` (14 tests: the
   `_decision_to_order` mapping in isolation, `execute_decisions`'s orchestration with
   a fake gate, `run_decision`'s full-loop wiring including the kill-switch preflight
   and advisory-note logging), `tests/runner/test_main.py` (6 tests: entrypoint wiring
   and failure classification, all with injected fakes — no live `claude` process, no
   live E*Trade calls), `tests/scripts/test_generate_plist.py` (10 tests: template
   rendering, file writing, printed commands, and hour/minute/template validation).

**Gates at close:** 259 unit tests (up from 226 at Phase 3 close: +3
`build_runtime`/`Runtime`/renewal, +14 `decision_run.py`, +6 `__main__.py`
entrypoint, +10 `generate_plist.py`), 46 wall tests (28 caps wall + 7 Phase 1 fixture
wall + 4 Phase 3 pipeline wall + 7 Phase 4 run wall, new this phase) — all passing.
`ruff check`, `ruff format --check`, `mypy --strict` — all clean across 30 source
files (up from 28 at Phase 3 close: `runner/decision_run.py`, `runner/__main__.py`).
spec-compliance checklist walked before committing (T1 grep-verified — `place_from_binding`
still has exactly one production caller, `server/tools.py::place_order`, unchanged
this phase; `decision_run.py` never imports `server.safety` directly, only receives
the gate via the injected `Runtime`; T2 grep-verified the same way; T3 — new log
lines all route through the existing `logs.log()` redaction, no raw secret ever
constructed or printed directly; T4 — the wall's own receipt tests trace
`reasoning_summary`/`signals_json`/`caps_snapshot_json` end to end; T5 — no cap/
pilot-capital default anywhere in the new code, `load_config` still runs first in
`build_runtime`; T6 — `_decision_to_order` hardcodes `security_type=EQ`,
`order_type=MARKET`, and only maps BUY/SELL, never a short or an option).

## What drifted from spec (and why)

- **The run wall's own fixtures caught a two-symbol assumption bug during authoring,
  not a design defect.** `VALID_CONFIG_TOML` (shared across every phase's tests)
  whitelists both `SPY` and `AAPL` (tier1) — the first draft of the "complete
  receipts" wall test assumed a single decision (`SPY` only), because the reused
  Phase 3 fixtures are SPY-labeled. `run_decision`'s pipeline correctly runs once per
  *whitelisted* symbol (two, per the shared config), producing two decisions and two
  executed trades — exactly right. Fixed by asserting on both rows rather than
  assuming one. Left in this report because it's exactly the kind of thing a wall
  exists to catch, even against fakes.
- **A second wall-authoring bug, same root cause as the fix above but worth naming
  separately: forgetting a fresh DB ships kill-switch ENGAGED by default (SPEC §4.3).**
  The `daily-trade-limit` scenario's first draft didn't explicitly disengage the kill
  switch, so every attempt correctly refused at `kill-switch` (checked first among
  `check_place`'s halts) before ever reaching `daily-trade-limit` — the wall was red
  for the right reason (the gate did exactly what §4.2's evaluation order specifies),
  not because of a bug in `execute_decisions` or the gate. Fixed by explicitly
  disengaging the switch in that test's setup, matching what the "complete receipts"
  test already did. No production code changed for either of these two fixes — both
  were test-setup corrections, confirmed by re-deriving the expected outcome from
  SPEC §4.2's evaluation order before touching anything (safety-wall skill: the safe
  direction is always "the gate is right, the test is wrong until proven otherwise").
- **`build_runtime`'s unconditional best-effort renewal required updating three
  existing Phase 1/2 `create_app` tests to stay hermetic.** Three of
  `tests/server/test_app.py`'s pre-existing tests didn't monkeypatch
  `oauth.signed_session` (relying instead on `ETRADE_ACCOUNT_ID_KEY` being set to
  avoid `EtradeClient.connect`'s auto-resolve network call) — with renewal now wired
  in unconditionally, those same three tests would have made a real HTTPS call to
  `api.etrade.com` with garbage fake credentials on every test run. Each got one added
  line (`monkeypatch.setattr(".../oauth.renew_tokens", lambda tokens: tokens)`) to
  preserve the module's own documented "Hermetic" claim — no assertion in any of the
  three changed.

## Open threads for Phase 5 (and beyond)

- **`runner/status.py` is still Phase 5's** — `write_status_report` still raises
  `NotImplementedError("Phase 5 (SPEC §7)")`, untouched this phase per the kickoff
  prompt's explicit scope boundary. The richer per-run status JSON SPEC §9 describes
  (run id, decisions, orders, refusals, duration, errors) is Phase 5's to build; this
  phase's JSONL advisory-notes log is the minimal durable receipt, not that surface.
- **No live end-to-end run has happened yet.** Every claim in this report is proven
  against fakes (unit tests) or fixture-recorded responses (the wall) — the first time
  a real `claude -p` WebSearch call, a real E*Trade sandbox quote/preview/place, and a
  real trader `Decision` flow through the whole chain together, unattended, under
  launchd, is still ahead. Per the kickoff prompt's standing warnings and the
  sandbox-prod skill: before that first live run, (1) verify `claude` + Max OAuth
  actually authenticate cleanly under launchd's stripped-down `PATH`/env (SPEC §9's
  own explicit precondition — a distinct concern from E*Trade's OAuth, and one that
  could still force a rethink if it fails), and (2) check the *actual* current
  kill-switch state in `config/trading.db` (ships engaged on a fresh DB, but this
  repo's DB isn't fresh) rather than assuming either state.
- **launchd plist not yet installed/scheduled** — `scripts/generate_plist.py` is
  implemented and hand-verified (renders correctly, writes to a `~/Library/
  LaunchAgents`-shaped path, prints correct `launchctl load`/`unload` commands against
  a throwaway `HOME` in this session), but no plist has been installed against the
  real `~/Library/LaunchAgents` yet, and no `launchctl load`/`start` has been run.
  That's an operator action per the plan's verification section, not something this
  session performed unattended.
- **`renew_tokens()` — now wired** (ADR-0005 point 2), resolving the SPEC §10 open
  question. ✔
- **Advisory-risk notes — now durable** (JSONL via the runner's log step), resolving
  Phase 3's open thread. ✔
- **`realized_pnl` still 0** — re-deferred a third time (ADR-0005 point 3); still
  coupled to real fills/cost-basis tracking, which sandbox can't meaningfully exercise
  and this phase's scope was orchestration, not position-tracking.
- **Every other Phase 1/2/3 open thread not mentioned above** (malformed-tool-input
  `ToolError` leakage — partially mitigated for the runner's own path, since
  `__main__.py`'s catch-all now prevents any exception, including one surfaced as a
  raw `ToolError`, from reaching launchd's stderr unformatted, but the underlying MCP
  `ToolError`-wrapping behavior for a live `claude -p` agent session is untouched;
  `scripts/remote_listener.py` unscheduled; phone control needing `.env`
  provisioning; `positions_cache` unpopulated, deliberate; sandbox canned-data
  limitations) is untouched by this phase's scope and remains exactly as
  `PHASE1-REPORT.md`/`PHASE2-REPORT.md`/`PHASE3-REPORT.md` left it — not re-litigated
  here to avoid the list silently drifting from what's actually still true.
- **Real cap numbers + pilot capital** — still open per SPEC §10, unchanged a fourth
  time; this phase, like Phases 1–3, builds and tests entirely against
  `tests/conftest.py::VALID_CONFIG_TOML`. Going live with real money is Phase 6's
  scope, not this one's.
