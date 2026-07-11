# Phase 5 Report — Observability

**Date:** 2026-07-11
**Status:** closed (wall unaffected by a phase-close ADR since Phase 5 has no wall of
its own — every Phase 5 assertion folds into the already-blocking-or-informational
`tests/wall/phase4/` job, per SPEC §7's own table)

## What shipped

Per SPEC §7 Phase 5's deliverable list (§9):

1. **Spike ADR (ADR-0006)** — the four kickoff-mandated Step 0 decisions, confirmed
   with Rishi before any loop logic was written: daily digest is store-backed
   (`read_caps_state`, not the in-memory `RunSummary`) and fires at the end of
   `run_decision`'s happy path; status reports are written on every exit path,
   best-effort; the breaker-tripped notification fires **at the source**
   (choice (b) — SPEC §3.1 amended so `server/` may import `notify`), the more
   architecturally invasive of the two options but the one that closes the gap a
   manual `.mcp.json` `place_order` would otherwise leave un-notified; and the daily
   digest's P&L line labels live unrealized P&L distinctly from still-untracked
   realized P&L (ADR-0005) rather than printing a misleading `$0.00`. Also batched:
   relocating `NotifyFn`/`build_notify` from `runner/decision_run.py` to
   `notify/ntfy.py` (the mechanical consequence of choice (b) — `server/` needed a
   way to build a `NotifyFn` without importing `runner/`), and "no new `phase5` wall
   marker."
2. **`runner/status.py`** (real, was a stub) — `build_status_report` (pure: run id,
   stage, `ts_utc`, duration, decisions/orders/refusals/errors, from a
   `RunSummary | None`) and `write_status_report`/`write_status_report_best_effort`
   (I/O: writes `status/<run_id>.json`, routes the serialized report through
   `logs.redact` before writing — T3 — and never raises on the caller's behalf).
3. **Daily digest** — `runner/decision_run.py::_send_daily_digest`, wired into
   `run_decision`'s happy path (wrapped in its own try/except: observability must
   never fail an otherwise-successful run). Reports trades executed/remaining,
   configured per-trade/daily-loss caps, live unrealized P&L (via a newly shared
   `etrade.models.unrealized_pnl`), and breaker state (ARMED/TRIPPED) — via the
   existing `notify/ntfy.py::send`, no new notification mechanism.
4. **Breaker-tripped notification** — `server/safety.py::ConfiguredSafetyGate` takes
   an optional `notify: NotifyFn` (default no-op); the loss-breaker gate's
   fresh-trip branch (never the already-tripped branch) fires it through a
   `_safe_notify` wrapper that swallows any notify exception so a channel outage can
   never mask `gate="loss-breaker"` behind a generic `"internal-error"` refusal.
   `server/app.py::build_runtime` resolves a `NotifyFn` (from `NTFY_TOPIC` or an
   injected one) and wires the *same* instance into both the gate and the returned
   `Runtime.notify` — one instance, never two divergently-built ones, matching
   ADR-0005's "one gate" discipline. `make_run_id()` was extracted from
   `build_runtime` so `runner/__main__.py` can mint a `run_id` before `build_runtime`
   is even attempted, letting a startup-failure status report and a
   would-have-been-successful `Runtime` share exactly one id.
5. **`runner/__main__.py`** — every one of its three failure branches
   (claude-unavailable, `ConfigError`, `ServerStartupError`) now routes through a
   local `_fail` helper that logs, notifies, and writes a best-effort status report
   with a distinct `stage` value, then returns 1; the `run_decision` catch-all does
   the same for an unexpected exception. `run_decision` itself writes its own report
   on both paths it can return through (`stage="completed"`,
   `stage="skipped-kill-switch"`) — no path double-writes, since an exception inside
   `run_decision` always occurs before its own status-write call.
6. **Confirmed, not rebuilt**: trade-executed (`execute_decisions`) and
   kill-switch-engaged/disengaged (`scripts/kill_switch.py`,
   `scripts/remote_listener.py`) notifications — untouched this phase, still work.
7. **Run wall extension** (`tests/wall/phase4/test_run_wall.py`, no new marker) —
   test #1 (full pipeline, complete receipts) extended to assert a written
   `status/<run_id>.json` with the full §9 field shape and a fired daily-digest
   notification; a new scenario (#8) proves a loss-breaker trip against the **real**
   gate fires exactly one "breaker" notification even across two refused orders the
   same day (the already-tripped branch never re-notifies). 8 phase4 wall tests now
   (was 7).
8. **Regular unit tests** — `tests/etrade/test_models.py` (new, 3 tests:
   `unrealized_pnl`), `tests/runner/test_status.py` (new, 10 tests:
   `build_status_report`/`write_status_report`/`write_status_report_best_effort`,
   including a redaction test), `tests/server/test_safety.py` (+4: fresh-trip
   notifies, already-tripped doesn't re-notify, a raising notify never masks
   `gate="loss-breaker"`, the `notify` param stays optional), `tests/server/test_app.py`
   (+4: `Runtime.notify` is callable and defaults sanely, `build_runtime` passes the
   *same* injected notify into both the gate and `Runtime`, `build_runtime` accepts
   an explicit `run_id`, `make_run_id()` returns unique strings), `tests/runner/test_main.py`
   (+6: a status report gets written on each of the three failure stages, on
   kill-switch skip, and the run_id is stable between a failed run and its own
   report — plus every pre-existing `main(...)` call updated to pass an explicit
   tmp-scoped `status_dir` so the default `Path("status")` never writes into the
   real repo tree during a test run).

**Gates at close:** 286 unit tests (up from 259 at Phase 4 close: +3
`test_models.py`, +10 `test_status.py`, +4 `test_safety.py`, +4 `test_app.py`, +6
`test_main.py`), 47 wall tests (28 caps wall + 7 Phase 1 fixture wall + 4 Phase 3
pipeline wall + 8 Phase 4 run wall, +1 this phase) — all passing. `ruff check`,
`ruff format --check`, `mypy --strict` — all clean across 30 source files (unchanged
count: this phase filled in an existing stub, `runner/status.py`, and extended
existing modules rather than adding new source files). spec-compliance checklist
walked before committing (T1 — the injected `NotifyFn` is a side effect fired
*after* `trip_breaker` and *after* the `Refusal` is determined, never read back into
a gate check, and `_safe_notify`'s own try/except plus `build_notify`'s
never-raise contract mean a notify failure can never change a refusal outcome,
proven directly by `test_check_place_never_fails_when_the_injected_notify_raises`;
T3 — `write_status_report` routes every report through `logs.redact` before
writing, proven by `test_write_status_report_redacts_secret_values`, and the
digest/breaker-notify message bodies are also redacted before being handed to
`notify`). Hand-verified against a real fixture-driven run (standing warning): the
actual written `status/<run_id>.json` and digest notification body were read and
eyeballed, not just asserted on in a test — confirmed correct field shape, correct
"unrealized ... (live)" / "realized not yet tracked (ADR-0005)" labeling, and no
secret values present.

## What drifted from spec (and why)

- **`Runtime.notify` needed a default, `Runtime.run_id` didn't.** The first pass at
  adding `notify: NotifyFn` as a required field on the frozen `Runtime` dataclass
  broke 8 existing tests across `tests/runner/test_decision_run.py` and
  `tests/wall/phase4/test_run_wall.py` that construct `Runtime(...)` directly
  (bypassing `build_runtime`) and never cared about notifications. Rather than edit
  every such call site, `Runtime.notify` got a safe no-op default
  (`_default_notify`) — production `build_runtime` always passes a real one
  regardless, so this loses no safety, and only the tests that specifically exercise
  notify behavior (the wall's new `_runtime(..., notify=...)` param, threaded
  separately into `ConfiguredSafetyGate`) needed to supply one. No production code
  behavior changed; this was purely a test-ergonomics call made once, during
  authoring, not a walked-back safety decision.
- **`build_notify`'s log lines change `agent_id` from `etrade-runner` to
  `etrade-notify`.** Moving the function out of `runner/decision_run.py` into the
  now-shared `notify/ntfy.py` meant its own internal warning logs (`NTFY_TOPIC not
  set`, `notification send failed`) could no longer honestly claim to be the
  runner's own log lines — they're now shared by both the runner and
  `server/app.py::build_runtime`. Renamed the constant rather than leave a stale
  label. No test asserted on the old `agent_id` value (confirmed by grep before the
  change), so this was a zero-risk rename, not a behavior change any caller depends
  on.
- **`ruff format` reflowed three files after the first implementation pass** (two
  `E501` line-length violations in `runner/__main__.py`/`runner/status.py`'s
  docstrings/signatures, one in a new `tests/runner/test_main.py` call). Caught by
  the `ruff check`/`ruff format --check` gate before commit, exactly as the CLAUDE.md
  gate sequence is meant to catch — no logic changed by the reformatting, re-ran the
  full suite afterward to confirm.

## Open threads for Phase 6 (and beyond)

- **`realized_pnl` still 0** — re-deferred a fourth time (ADR-0005 point 3, restated
  ADR-0006 point 4); the digest now labels this gap honestly rather than hiding it,
  but closing it for real still needs real fills/cost-basis tracking, which stays
  Phase 6's (or a dedicated position-tracking phase's) scope.
- **No live end-to-end run has happened yet** — carried forward from Phase 4,
  unchanged this phase. This phase's own status/digest surfaces were hand-verified
  against a fixture-driven run (see "Gates at close" above), not a live one. Before
  the first live run: verify `claude` + Max OAuth authenticate cleanly under
  launchd's stripped-down `PATH`/env, and check the *actual* current kill-switch
  state in `config/trading.db` (not assumed).
- **launchd plist still not installed/scheduled** against the real
  `~/Library/LaunchAgents` — unchanged from Phase 4, an operator action.
- **`renew_tokens()` wired but never exercised by a live E*Trade call** — unchanged
  from Phase 4, only tests/fakes have exercised it so far.
- **`scripts/remote_listener.py` still unscheduled**, phone control still needs
  `NTFY_COMMAND_SECRET` provisioning in `.env` — unchanged, carried forward again.
- **`positions_cache` still unpopulated** (deliberate, ADR-0003) — unchanged.
- **Sandbox canned-data limitations** (`place_from_binding` always reports
  `status="OPEN"`, `filled_quantity=0`) — a status report's `"orders"` field will
  reflect that canned shape, not a real fill, until Phase 6's prod cutover.
- **Real cap numbers + pilot capital** — still open per SPEC §10, unchanged a fifth
  time; this phase, like every phase before it, builds and tests entirely against
  `tests/conftest.py::VALID_CONFIG_TOML`.
- **Phase 6's own scope, restated from SPEC §7**: prod cutover checklist (per the
  sandbox-prod skill), schema-drift re-run against live prod response shapes, the
  fixed 2–4 week evaluation window, SPY benchmark comparison, and — the one item this
  phase's own work is a direct prerequisite for — `realized_pnl` finally closing once
  real fills exist, at which point the digest's P&L line can drop its "not yet
  tracked" label and report a genuine realized figure.
