# 0006 — Phase 5 observability: digest source, status-report scope, breaker-notify shape, digest P&L presentation

**Date:** 2026-07-11
**Status:** accepted

## Context

Phase 5 (SPEC §7) makes real three SPEC §9 observability promises Phase 4 left as
stubs or gaps: `runner/status.py::write_status_report` (per-run status JSON — run id,
decisions, orders, refusals, duration, errors), the daily digest (trades, P&L, caps
remaining), and a distinct "breaker tripped" notification (today a trip only writes
DB state via `StateStore.trip_breaker` and a generic JSONL refusal log line,
indistinguishable at the notification layer from any other refused order). SPEC §7's
own table marks this phase "(folds into run wall)" — no new named wall — but the
kickoff prompt required four judgment calls be proposed, confirmed with Rishi, and
ADR'd before writing loop logic, batched here per the adr-writing skill.

## Decision

**1. Daily digest: store-backed, fired at the end of `run_decision`'s happy path.**
`runner/decision_run.py::_send_daily_digest` reads `rt.state.read_caps_state(today_utc())`
— the same `caps_state` row the loss-breaker and daily-trade-limit gates themselves
read — rather than the in-memory `RunSummary`. This stays accurate on a day with a
re-run, a manual `.mcp.json` place_order, or (eventually) a higher-cadence pilot,
since those all bump the same `caps_state` row the digest reads back. No new
`trade_log` query method was needed: `caps_state.trades_executed` already is the
authoritative day-level executed count (`store/state.py`, unchanged this phase).

**2. Status reports: written on every exit path, best-effort.** `runner/status.py`
gains `build_status_report(run_id, summary, *, stage, duration_seconds, errors=None)`
(pure) and `write_status_report`/`write_status_report_best_effort` (I/O, T3-redacted,
never raises). `run_decision` writes one on both paths it can return through
(`stage="completed"` and `stage="skipped-kill-switch"`); `runner/__main__.py::main`
writes one on each of its three startup/unexpected-exception failure paths
(`stage="claude-unavailable"|"config-error"|"startup-error"|"unexpected-exception"`),
via a local `_fail` helper. A `run_id` is now minted up front by
`server/app.py::make_run_id()` (extracted from `build_runtime`) and passed into
`build_runtime(run_id=...)`, so a failed run's status report and a would-have-been
Runtime share exactly one id. `summary=None` on every failure/skip path yields a
report with zeroed counts and an empty `orders`/`refusals` list, never a fabricated
non-zero figure. `run_decision` never writes twice: an exception during pipeline/
execute/log always occurs before its own status-write call, so `__main__.py`'s
catch-all is the only writer for that path.

**3. Breaker-tripped notification: (b) notify at the source.** SPEC §3.1's module map
is amended: `server/` may now import `notify` (see SPEC §3.1 edit, same commit).
`ConfiguredSafetyGate.__init__` takes an optional `notify: NotifyFn = <no-op>`; the
**fresh-trip branch only** of `_check_loss_breaker` (never the already-tripped
branch) calls a `_safe_notify` wrapper right after `self._state.trip_breaker(day)` and
before returning the `Refusal` — so it fires at most once per UTC day regardless of
how many subsequent orders get refused that day, and regardless of caller: the
runner's `execute_decisions` loop, or a manual `.mcp.json` `place_order`. Chosen over
(a) "the runner detects it from the refusal payload" because a manual `place_order`
bypasses the runner entirely — (a) would leave that path silently un-notified, a real
gap the kickoff prompt asked to be named explicitly if chosen; (b) closes it for every
caller with one code path, at the true source of the state transition, matching how
kill-switch engage/disengage and trade-executed already notify at their own source
(`scripts/kill_switch.py`, `scripts/remote_listener.py`, `execute_decisions`).

`NotifyFn`/`build_notify` moved from `runner/decision_run.py` to `notify/ntfy.py` (no
behavior change beyond the log line's `agent_id`, `etrade-runner` → `etrade-notify`,
since the function is no longer runner-specific) — this is what lets `server/app.py`
build a `NotifyFn` without importing `runner/` (the "nothing imports `runner/`" rule
in SPEC §3.1 is unchanged). `server/app.py::build_runtime` resolves
`notify` from `NTFY_TOPIC` when the caller doesn't inject one, and wires the *same*
instance into both `ConfiguredSafetyGate` and the returned `Runtime.notify` field —
one NotifyFn, never a second, divergently-built one, the same "one enforcement setup"
discipline ADR-0005 established for the gate itself.

T1 requires the injected notify to never influence the refusal outcome. `_safe_notify`
enforces this structurally: it wraps `self._notify(...)` in its own try/except (not
relying on the caller's `NotifyFn` to be well-behaved), so a raising notify is caught
and logged, never propagated up into `check_place`'s own broad
`except Exception: return self._fail_closed_refusal(exc)` — which would otherwise mask
`gate="loss-breaker"` behind a generic `"internal-error"` refusal *after* the breaker
had already tripped in the DB, the worst of both worlds. Proven by
`tests/server/test_safety.py::test_check_place_never_fails_when_the_injected_notify_raises`.

**Known gap, named explicitly, not silently accepted:** none — choice (b) closes the
manual-`place_order` gap choice (a) would have left open, which was the whole reason
to prefer it. The only residual gap is a notify-channel *outage* (e.g. ntfy.sh itself
down), which degrades to a logged warning by design (T1: a monitoring gap is never
grounds to change or re-attempt an already-decided refusal).

**4. Digest P&L: label realized + show unrealized (live).** `_send_daily_digest`
reports live unrealized P&L via a newly shared `etrade.models.unrealized_pnl(positions)`
— the identical calculation `server/safety.py::ConfiguredSafetyGate._unrealized_pnl`
now delegates to, so the digest and the loss-breaker gate can never silently drift
apart into two different numbers — explicitly labeled `"unrealized ... (live)"`, plus a
plain `"realized not yet tracked (ADR-0005)"` rather than a fabricated `$0.00` that
would misrepresent `caps_state.realized_pnl`'s still-hardcoded-zero state (re-deferred
a third time, ADR-0005 point 3) as a real, tracked figure. `unrealized_pnl` fetches
positions via `rt.client.get_positions()` — a read-only call, strictly downstream of
whatever the pipeline/gate already decided (T1: nothing built this phase reads back
into a gate check).

**No new `phase5` wall marker.** SPEC §7's own table marks this phase "(folds into run
wall)" — every Phase 5 assertion (status report written, digest notified, breaker-trip
notification fired exactly once) extends the existing `tests/wall/phase4/` scenarios
rather than spiking a parallel `tests/wall/phase5/` directory. No CI selector change
was needed.

## Alternatives

- **Point 1 — reuse `run_decision`'s in-memory `RunSummary` for the digest**: rejected;
  cheaper, but silently wrong on any day with more than one run or a manual
  `place_order` — the digest would report only the last run's numbers, not the day's.
- **Point 2 — status reports on completed runs only**: rejected; SPEC §9 lists
  "errors" as a status-report field, implying failed runs get a report too, and a run
  that dies at startup (missing caps, missing OAuth tokens) is exactly the case an
  operator most needs a durable record of.
- **Point 3(a) — the runner detects the breaker trip from the refusal payload**:
  rejected; requires no `server/` import change, but leaves a manual `.mcp.json`
  `place_order` breaker trip permanently un-notified — a real, if narrow, gap for a
  phase whose whole job is closing exactly this kind of gap.
- **Point 4 — omit P&L from the digest entirely this phase**: rejected; unrealized
  P&L is a real, live, already-computed number (the loss-breaker's own gate input) —
  omitting it entirely throws away legitimate signal for the sake of avoiding a
  labeling problem that a clear label solves more usefully.
- **Point 4 — print `realized_pnl` as `$0.00` unlabeled**: rejected outright per the
  kickoff prompt's own framing — a digest a human reads is a different accuracy bar
  than a number a gate compares against a threshold; a zero that looks tracked but
  isn't is worse than no number at all.

## Consequences

- SPEC §3.1's module-import table changes: `server/` may now import `notify` (was
  `etrade, store, config, logs`). `notify/ntfy.py` gains `NotifyFn`/`build_notify`
  (moved from `runner/decision_run.py`); `server/app.py::build_runtime` and
  `runner/__main__.py::main` both import from `notify/ntfy.py` now, not from each
  other's module.
- `ConfiguredSafetyGate.__init__` gains an optional `notify` keyword (default no-op) —
  every existing three-positional-arg construction across the test suite kept working
  unchanged; only tests that specifically exercise breaker-notify behavior pass one.
- `server/app.py::Runtime` gains a `notify: NotifyFn` field (default a safe no-op, so
  plain `Runtime(...)` test constructions that don't care about notifications need no
  edits) and a new `make_run_id()` function; `build_runtime` gains optional
  `notify=`/`run_id=` keyword params.
- `etrade/models.py` gains `unrealized_pnl(positions) -> float`, the single shared
  definition both the gate and the digest read from.
- `runner/status.py` is no longer a stub; `status/<run_id>.json` is a new durable
  artifact this repo produces on every decision-run attempt, gitignored the same way
  `logs/`/`trading.db` already are (no code change needed — `status/` matches no
  tracked-file pattern already, confirmed no accidental commit risk).
- Every Phase 5 assertion lives inside `tests/wall/phase4/test_run_wall.py` (8 wall
  tests now, up from 7) rather than a new `tests/wall/phase5/` — if a future phase
  needs its own named wall, this is the precedent for "folds into" phrasing meaning
  literally that, not a parallel-but-smaller wall.
- `realized_pnl` remains 0 through Phase 5's close (ADR-0005 point 3, carried forward
  a fourth time, not silently dropped) — the digest's labeling makes this an honest
  gap in the reader-facing output rather than a hidden one.
