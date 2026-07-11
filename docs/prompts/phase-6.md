# Phase 6 Kickoff Prompt — Pilot

Paste this to start a Phase 6 session. Model: `opusplan` (pinned in `.claude/settings.json`).

---

You are implementing **Phase 6 of SPEC §7** for the etrade-agent repo: the pilot —
prod cutover checklist (sandbox-prod skill), a schema-drift re-run against live prod
response shapes, a fixed-window live run, an SPY benchmark comparison, and a
decision-gate report. Operating doctrine, model policy, and invariants are in
CLAUDE.md; T1–T6 are non-negotiable. Phase 5 (observability: status reports, daily
digest, breaker-tripped notification) is done and merged (`3b3d9fb`) — read
`docs/PHASE5-REPORT.md` and `docs/decisions/0006-phase5-observability.md` before
touching anything.

**This phase is different in kind from Phases 1–5, not just in degree.** Every prior
phase built and proved itself entirely against sandbox, fixtures, and fakes — SPEC §7
lists no wall for this phase (dash in the table) because the acceptance bar for a
pilot isn't a pytest suite, it's real money moving correctly under real market
conditions for a fixed window. Read the **sandbox-prod** skill in full before doing
anything else this session; its "Before Anything Touches Prod" checklist is not
optional guidance, it is the enforcement mechanism for this phase (the same role a
wall plays in Phases 1–4).

## Gate before the gate — inputs only Rishi can supply

Unlike every prior phase's Step 0 (propose an approach, confirm, ADR), the items below
have **zero design freedom** — T5 forbids a default for any of them, and no amount of
architecture skill substitutes for Rishi actually providing them. Do not propose values
for any of these; ask directly and wait.

1. **Has sandbox ever been live-verified end-to-end?** Every phase report through
   Phase 5 carries "no live end-to-end run has happened yet" as an open thread — every
   test, every wall, every hand-verification this repo has ever run went through
   fixtures or fakes, never a real `claude -p` call against a real (even sandbox)
   E*Trade API. **This must happen, in sandbox, before any prod discussion starts** —
   it's the sandbox-prod skill's own step 2 ("before Phase 6 opens, the answer is no").
   If it hasn't happened: that's this session's first action, not prod cutover.
2. **Prod E*Trade access.** Is there an approved prod consumer key/secret (separate
   from the sandbox ones already in `.env`)? Has the prod OAuth dance
   (`scripts/oauth_login.py` against a prod base URL) ever been run? E*Trade's prod
   API approval is a separate process from sandbox approval and can itself take
   days–weeks (SPEC §10's original sandbox-key caveat applies again, once, for prod).
3. **Real cap numbers + pilot capital** (SPEC §10, §8.1) — `pilot_amount_usd`,
   `per_trade_pct`, `daily_trade_limit`, `daily_loss_pct`. No defaults exist anywhere
   in this codebase (T5); the server refuses to start without them. These need to be
   real numbers Rishi is prepared to lose (SPEC §1: "fully-loss-tolerant... isolated
   from core holdings and the Roth IRA").
4. **The fixed 2–4 week evaluation window's actual start/end dates** (SPEC §1: "defined
   **before** starting"). SPEC §1 is explicit that the Phase 6 signal only works if
   there is no mid-run intervention — picking dates now, before the window opens, is
   what makes "no intervention" a kept commitment rather than a vague intention.

Everything below assumes these four are answered. If any is still open, stop and
surface that rather than drafting checklist content around a placeholder.

## Step 0 — Open-questions gate (design decisions — propose, confirm, ADR)

Once the inputs above exist, these still need a genuine design decision, same pattern
as Phases 1–5:

1. **Attended or unattended for the first N days of the live window?** The launchd
   plist (`scripts/generate_plist.py`, Phase 4) has never been installed against the
   real `~/Library/LaunchAgents` — decide whether the pilot's very first live run(s)
   are triggered manually (`uv run python -m etrade_agent.runner`, watched) before
   handing off to the unattended launchd schedule, or whether launchd goes live from
   day one. Given this is also the first-ever live E*Trade call this codebase makes,
   recommend: manually triggered and watched for at least the first live run (sandbox
   or prod), launchd only after that run's receipts are hand-verified correct.
2. **Schema-drift re-run: sandbox re-recording, prod recording, or both?** SPEC §5.4:
   fixtures are canned sandbox responses; §7's Phase 6 row requires a schema-drift
   re-run "against live prod shapes" before any real order. Decide the exact mechanic:
   re-run `scripts/record_fixture.py` against live sandbox first (cheap, proves the
   recording pipeline itself still works), then a smaller, deliberate set of prod
   reads (`get_quote`/`get_positions`/`get_balances` — read-only, no order-mutating
   call) to catch any prod-vs-sandbox shape drift before `preview_order`/`place_order`
   ever get pointed at prod.
3. **Decision-gate report: what does "scale / keep flat / adjust / shut down" actually
   score against?** SPEC §1 names the four outcomes but not the criteria. Decide (and
   ADR) the actual comparison: pilot P&L vs. SPY buy-and-hold over the identical
   window, ratio of refused-to-attempted decisions, any qualitative signal from
   `risk_advisory_llm` notes, whether `realized_pnl` (still 0 through Phase 5,
   ADR-0005 point 3) can finally close this phase using real fill data now available.
4. **Does `scripts/remote_listener.py` get scheduled this phase?** It's been buildable
   since Phase 2 and unscheduled through every phase since (ADR-0003 point 5) — decide
   whether real money live for 2–4 weeks is the point at which phone-based kill-switch/
   breaker-reset control finally becomes operationally worth turning on, which also
   means provisioning `NTFY_COMMAND_SECRET` in `.env` (`scripts/generate_totp_secret.py`)
   for real, not just in tests.

Propose each, confirm with Rishi, batch into one ADR before writing the cutover
checklist itself.

## Context to load (context diet — nothing more)

- CLAUDE.md (invariants + doctrine, **especially** the sandbox-prod skill's checklist
  and the Trading Standing Orders section), `docs/PHASE5-REPORT.md`,
  `docs/decisions/0006-phase5-observability.md`
- SPEC §1 in full (goals, the fixed evaluation window, the "no mid-run intervention"
  constraint, non-goals), §7 Phase 6 row, §10 (every remaining open question — this
  phase should close most of them for the first time in this repo's history), §5.4
  (fixture/schema-drift discipline), §9 (operations — what launchd/notifications need
  to be actually running, not just built)
- `docs/PHASE1-REPORT.md` through `docs/PHASE5-REPORT.md`'s "Open threads" sections —
  every carried-forward item across five phases converges on this one; read them
  rather than re-deriving what's still open from scratch
- The **sandbox-prod** skill (`.claude/skills/sandbox-prod/SKILL.md`) in full — its own
  text says "the Phase 6 cutover gets its own written checklist (drafted at Phase 6
  planning); this skill is the interim guard, not a substitute for it." Drafting that
  checklist is this phase's first real deliverable.
- Already-real, already-tested surfaces this phase exercises for the first time
  against live data rather than fixtures: `runner/__main__.py::main` (the whole
  fetch→pipeline→execute→log→notify loop, Phase 4), `runner/status.py`/the daily
  digest (Phase 5 — these should now report a real duration and a real digest against
  real caps_state, not fixture-driven numbers), `server/app.py::build_runtime` (the
  single construction path — confirm it still enforces identically against a prod
  base URL; nothing about `ConfiguredSafetyGate` should need to change to go live,
  since T1 means the gate never knew or cared which environment it was protecting)

## Deliverables (SPEC §7 Phase 6 row)

1. **Prod cutover checklist** — a real, written, walked-through document (not just the
   sandbox-prod skill's interim bullet list), covering at minimum: environment
   verification, kill-switch state verification, caps sanity, notification-path
   liveness check, and the schema-drift re-run's actual pass/fail criteria.
2. **Schema-drift re-run** — executed for real, per Step 0 #2's decided mechanic,
   before any order-mutating prod call.
3. **Fixed-window live run** — the actual 2–4 week pilot, running under the caps and
   dates fixed in the "gate before the gate" section, unattended per Step 0 #1's
   decided handoff point.
4. **SPY benchmark comparison** — pilot performance vs. SPY buy-and-hold over the
   identical window.
5. **Decision-gate report** — scale / keep flat / adjust / shut down, scored per Step
   0 #3's decided criteria.

## Wall / test coverage (SPEC §7 Phase 6 row: no wall listed)

No new named wall — this phase's acceptance bar is the live run itself and the
decision-gate report, not a pytest suite. The schema-drift re-run reuses the existing
Phase 1 fixture-wall discipline (`tests/wall/phase1/`, `etrade-fixtures` skill) against
freshly recorded responses, rather than inventing new test infrastructure. If this
phase's work surfaces a real bug in `server/safety.py` or any other gate, that fix
still goes through the safety-wall skill and the existing caps wall exactly as any
other phase would — a live pilot finding a gate bug is not an excuse to patch around
the wall.

## Standing warnings

- **Everything in CLAUDE.md's Trading Standing Orders applies at full force, for the
  first time with teeth.** "Sandbox is the default environment everywhere" and
  "anything that could touch production requires the sandbox-prod skill first" have
  been true on paper since Phase 1; this is the first phase where getting either wrong
  has a real financial consequence, not a hypothetical one.
- **T1 is unchanged by environment.** `ConfiguredSafetyGate` does not know or care
  whether `rt.client` is pointed at sandbox or prod — if going live requires touching
  `server/safety.py` for any reason other than a genuine bug fix, stop and reconsider;
  the gate's correctness should already be fully proven by the existing caps wall.
- **T3 gets sharper teeth this phase.** Prod OAuth consumer keys/tokens are real
  financial credentials, not sandbox placeholders — the existing `.gitignore`/redaction/
  fixture-scrubbing controls (T3) are unchanged in mechanism but the cost of a mistake
  is no longer hypothetical. Re-verify the PreToolUse secret-guard hook is active
  before this session does anything with `.env`/`tokens/`.
- **"No mid-run intervention" (SPEC §1) is a commitment, not a suggestion.** Once the
  fixed window starts, resist the urge to tune caps, adjust the pipeline, or intervene
  on a bad day — that's exactly the behavior that would invalidate the evaluation
  signal the whole phase exists to produce. The kill switch and loss-breaker exist
  precisely so "don't intervene" doesn't mean "no safety net."
- **Carried-forward open threads, explicitly re-flagged one more time, not silently
  dropped:** `realized_pnl` still 0 through Phase 5's close (ADR-0005 point 3) — this
  phase is the first with real fills to finally close it, per Step 0 #3's
  decision-gate criteria; `positions_cache` still deliberately unpopulated (ADR-0003);
  `scripts/remote_listener.py` scheduling status per Step 0 #4.
- Full gates once before push, same as every prior phase: `uv run ruff check . &&
  uv run ruff format --check . && uv run mypy && uv run pytest` plus the wall run
  (`uv run pytest -m wall --override-ini "addopts="` — caps wall + Phase 1 fixture
  wall + Phase 3 pipeline wall + Phase 4 run wall, still all green; the schema-drift
  re-run's freshly recorded fixtures should replace, not duplicate, the Phase 1
  fixture-wall's existing recordings once verified current).
- Close the phase with `docs/PHASE6-REPORT.md` (same shape as Phases 1–5's) — this one
  additionally IS the decision-gate report, or references it directly: what the pilot
  actually did, how it compared to SPY, and which of scale/keep-flat/adjust/shut-down
  Rishi is choosing and why.
