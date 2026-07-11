# Phase 5 Kickoff Prompt — Observability

Paste this to start a Phase 5 session. Model: `opusplan` (pinned in `.claude/settings.json`).

---

You are implementing **Phase 5 of SPEC §7** for the etrade-agent repo: observability —
`runner/status.py` for real, the daily digest, and closing the "breaker tripped"
notification gap SPEC §9 promises but no phase has wired yet. Operating doctrine,
model policy, and invariants are in CLAUDE.md; T1–T6 are non-negotiable. Phase 4
(orchestration: the real decision-run loop, `runner/__main__.py`, the launchd plist,
the run wall proving "executes ≤ caps" against the real gate) is done and merged
(`2cbe0c5`) — read `docs/PHASE4-REPORT.md` and
`docs/decisions/0005-phase4-orchestration.md` before touching anything, especially the
open threads Phase 4 flagged for this phase.

**This phase is smaller in code surface than Phases 1–4** — SPEC §7's own table marks
it "(folds into run wall)," meaning there is no new named wall to spike (see the wall
section below, not skippable, just different in shape). Don't let that read as "less
rigor" — it still touches notification correctness or T1/T3 boundaries in a couple of
non-obvious places (see Step 0 #3).

## Step 0 — Open-questions gate (answer before writing code)

1. **Daily digest: what does it aggregate, and when does it fire?** SPEC §9 lists
   "daily digest (trades, P&L, caps remaining)" as its own notification event,
   alongside trade-executed/breaker-tripped/kill-switch. The pilot's cadence is once
   daily at market open (SPEC §9) — decide whether the digest is just an extra
   notification tacked onto the END of that same `run_decision` call (cheapest,
   reuses the in-memory `RunSummary` from `runner/decision_run.py`), or a separate
   query against `trade_log`/`caps_state` for the UTC day (SPEC §5.1;
   `StateStore.read_caps_state`/`today_utc()` already exist) so a day with a
   re-run, a manual `place_order` via `.mcp.json`, or a future higher-cadence pilot
   still produces an accurate day-level number rather than just "this one run's
   summary." Recommend: query the store, not the in-memory summary — cheap, correct
   regardless of how many runs happened, and doesn't couple the digest's accuracy to
   `run_decision` being the only way a trade ever gets attempted. Propose, confirm
   with Rishi, ADR.

2. **Status reports: written on every exit path, or only completed runs?** SPEC §9:
   the report includes "errors" as a field — implying a status report should exist
   even for a run that failed at startup (missing caps, missing OAuth tokens) or hit
   an unexpected mid-run exception, not just a clean completion.
   `runner/__main__.py::main` (Phase 4) currently has four distinct exit paths
   (claude-unavailable, config/startup error, unexpected run_decision exception,
   normal completion including the kill-switch-skip case) that each already send an
   ntfy alert — decide whether every one of them should *also* attempt a best-effort
   `write_status_report` call (mirroring the "never let an observability failure
   abort the run" resilience pattern Phase 3/4 established for the pipeline steps
   and `build_notify`), or whether status reports are scoped to completed runs only
   and startup failures rely on ntfy + `logs.py` alone. Propose, confirm, ADR (batch
   with #1).

3. **Where does the "breaker tripped" notification actually live?** Verified this
   session: `server/safety.py::ConfiguredSafetyGate._check_loss_breaker` calls
   `self._state.trip_breaker(day)` and returns a `Refusal` — nothing in `safety.py`
   or `server/tools.py` calls `notify/ntfy.py::send` for this specific state
   transition; it only ever surfaces as a generic `logs.log(..., "order-mutating
   tool call refused", gate="loss-breaker", ...)` JSONL line, indistinguishable at
   the notification layer from any other refusal. SPEC §9 lists "breaker tripped" as
   its own distinct event, same tier as "trade executed" and "kill switch
   engaged/disengaged" (both of which already notify distinctly — Phase 2's
   `reset_breaker.py`/`kill_switch.py` scripts, Phase 4's `execute_decisions` for
   trades). **This can't be fixed inside `server/safety.py` itself without a module-
   map change**: SPEC §3.1 lists `server/`'s allowed imports as `etrade, store,
   config, logs` — not `notify` — and grepping confirms no file under `server/`
   imports it today. Two shapes to decide between: **(a)** the gate's `Refusal`
   already carries `gate="loss-breaker"` in its payload — have the runner
   (`execute_decisions`, which already sees every refusal) detect that specific gate
   name and fire a distinct notification, no `server/` changes needed; **(b)** amend
   the module map to let `server/` import `notify` directly, so the trip is notified
   at the moment it happens regardless of which caller (the runner, a future direct
   MCP tool call, `scripts/record_fixture.py`) triggered it. (a) requires no
   contract change and keeps `server/`'s import list narrow (T1-adjacent: the gate
   already doesn't need to know *how* a refusal gets reported, only that it's
   correct); (b) is the more "notify at the source of truth" architecture but is a
   real SPEC §3.1 amendment for a phase whose SPEC row doesn't ask for one. Propose,
   confirm, ADR (batch with #1–#2). Note: a direct manual `place_order` MCP call
   (bypassing the runner entirely, via `.mcp.json`) can also trip the breaker — if
   you land on (a), name this explicitly as a known gap (a manual trip wouldn't
   notify) rather than silently accepting it.

4. **Digest P&L: does it silently imply more accuracy than exists?**
   `realized_pnl` is still 0 at every `caps_state` read (ADR-0005 point 3, deferred a
   third time this repo's history) — the loss-breaker itself already works fine off
   live unrealized P&L alone (ADR-0003 point 3), but a "P&L" figure in a *digest a
   human reads* is a different bar than a number a gate compares against a
   threshold. Decide whether the digest labels this limitation explicitly (e.g.
   "unrealized P&L: $X; realized P&L: not yet tracked, see ADR-0005") or omits a P&L
   figure from the digest entirely this phase rather than presenting a
   known-incomplete number as if it were the real one. Propose, confirm, ADR (batch
   with #1–#3).

## Context to load (context diet — nothing more)

- CLAUDE.md (invariants + doctrine), `docs/PHASE4-REPORT.md`,
  `docs/decisions/0005-phase4-orchestration.md` (the `build_runtime`/`Runtime`
  shared construction path and the `NotifyFn` seam this phase builds on)
- SPEC §9 in full (operations — the exact notification-event list, the status-report
  field list, the daily-digest phrase this phase makes real), §7 Phase 5 row (note:
  "(folds into run wall)" — no new named wall, see below), §5.1 (`trade_log`/
  `caps_state` schema — what the digest queries), §3.1 (module map — `server/`'s
  current import list, relevant to Step 0 #3)
- Current stubs: `runner/status.py::write_status_report` (raises
  `NotImplementedError("Phase 5 (SPEC §7)")`, docstring already names the shape:
  "Write status/<run_id>.json for the daily digest and monitoring")
- Real, already-built surface this phase extends rather than replaces:
  `runner/decision_run.py::RunSummary`/`OrderOutcome` (Phase 4 — decisions
  considered, orders skipped, per-order outcomes with `refusal_gate`; check whether
  it already carries enough for a status report or needs a small extension, e.g. a
  start/end timestamp for "duration"), `runner/decision_run.py::NotifyFn`/
  `build_notify` (the injectable notification seam every Phase 4 wall/unit test
  already uses — reuse it, don't invent a second notify path),
  `store/state.py::StateStore.read_caps_state`/`today_utc` (what a store-backed
  digest query reads from)
- Skills that will fire: adr-writing (Step 0's batched decisions), spec-compliance
  (T1 — a status/digest/notification path must never become a decision input, only
  ever downstream of one; T3 — status JSON / digest text must never leak
  `NTFY_TOPIC`/tokens/account-identifying values, even though it's "just
  observability"), safety-wall (only if you end up adding assertions inside the
  existing `tests/wall/phase4/` run wall — see below)

## Deliverables (SPEC §7 Phase 5 row + §9)

1. **Spike/decisions ADR** — the Step 0 decisions above, batched.
2. **`runner/status.py::write_status_report`** — implemented for real per its own
   docstring: run id, decisions, orders, refusals, duration, errors, written to
   `status/<run_id>.json`. Wire it into `runner/__main__.py`/
   `runner/decision_run.py` per Step 0 #2's answer.
3. **Daily digest** — trades, P&L (per Step 0 #4's answer), caps remaining
   (`daily_trade_limit - trades_executed`, `per_trade_pct`/`daily_loss_pct`
   headroom) — sent via the existing `notify/ntfy.py::send` (no new notification
   mechanism), through the same `NotifyFn` seam Phase 4 already built, per Step 0
   #1's answer on aggregation source and trigger point.
4. **Breaker-tripped notification** — closes the gap verified in Step 0 #3, shaped
   per that decision.
5. **Confirm (don't rebuild) that trade-executed and kill-switch-engaged/disengaged
   notifications already work** — Phase 4 wired trade-executed
   (`runner/decision_run.py::execute_decisions`); Phase 2 wired kill-switch
   engage/disengage (`scripts/kill_switch.py`, `scripts/remote_listener.py`). This
   phase's job is closing the two genuinely-missing events (breaker-tripped, daily
   digest) and building status reports — not re-touching what already works, unless
   testing surfaces a real bug.

## Wall / test coverage (SPEC §7 Phase 5 row: "folds into run wall")

No new `tests/wall/phase5/` directory or `phase5` marker — SPEC is explicit this
phase's correctness folds into the existing Phase 4 run wall rather than spiking its
own. Concretely this likely means: extend `tests/wall/phase4/test_run_wall.py`'s
existing scenarios with new assertions (e.g., the "complete receipts" test also
asserts a `status/<run_id>.json` gets written; a new scenario proves a loss-breaker
trip during `execute_decisions` produces a distinct notification) rather than
authoring a parallel test file. Regular (non-wall) unit tests cover
`write_status_report`/digest-query logic in isolation, same as `runner/decision_run.py`
got in Phase 4 (`tests/runner/test_decision_run.py`). If Step 0 concludes a genuinely
new acceptance-bar test is warranted, that itself is a decision worth naming in the
ADR — SPEC's phrasing is a strong hint, not a hard ban.

## Standing warnings

- **T1 still means none of this can become a decision input.** A status report, a
  digest, a breaker-tripped notification — all of it is strictly downstream of what
  `server/safety.py` already decided. Nothing built this phase may read back into a
  gate check or influence whether an order is attempted.
- **T3 still applies to "just observability" surfaces.** `status/<run_id>.json` and
  digest notification text both need the same scrutiny as any other log line —
  route through `logs.py`'s redaction where applicable, never construct a message
  that echoes `NTFY_TOPIC`, OAuth tokens, or `accountIdKey` verbatim, even
  incidentally (e.g. via a raw exception string — Phase 4's precedent: catch
  broadly, log the exception, never assume its string form is clean).
- **Carried-forward open threads, explicitly re-flagged, not silently dropped:**
  `realized_pnl` still 0 (ADR-0005 point 3 — this phase's Step 0 #4 is about how the
  digest *presents* that gap, not about closing it); real cap numbers / pilot
  capital still open (SPEC §10); no live end-to-end decision run has happened yet
  (Phase 4's own open thread — this phase doesn't require one, but a status
  report/digest this phase builds should be hand-verified against at least one real
  or fixture-driven run before calling it done); launchd plist still not installed
  against the real `~/Library/LaunchAgents` (operator action); `renew_tokens()` now
  wired (Phase 4) but only ever exercised by tests/fakes so far, not a live E*Trade
  call; `scripts/remote_listener.py` still unscheduled; phone control still needs
  `.env` provisioning (`NTFY_COMMAND_SECRET`); `positions_cache` still unpopulated
  (deliberate, ADR-0003); sandbox canned-data limitations (place responses always
  report `status="OPEN"`, `filled_quantity=0` — a status report's "orders" field
  will reflect that canned shape, not a real fill, until Phase 6's prod cutover).
- Full gates once before push: `uv run ruff check . && uv run ruff format --check .
  && uv run mypy && uv run pytest` plus the wall run
  (`uv run pytest -m wall --override-ini "addopts="`, must still include the caps
  wall, Phase 1 fixture wall, Phase 3 pipeline wall, and Phase 4 run wall green
  regardless of what this phase adds to the last one).
- Close the phase with a short `docs/PHASE5-REPORT.md` post-mortem (same shape as
  Phases 1–4's), noting what's left for Phase 6 (prod cutover checklist per the
  sandbox-prod skill, schema-drift re-run against live prod shapes, the fixed
  2–4 week evaluation window, SPY benchmark comparison, `realized_pnl` finally
  closing once real fills exist).
