# Phase 4 Kickoff Prompt — Orchestration

Paste this to start a Phase 4 session. Model: `opusplan` (pinned in `.claude/settings.json`).

---

You are implementing **Phase 4 of SPEC §7** for the etrade-agent repo: orchestration —
`runner/headless.py` in anger, the launchd plist installed, and the first full
sandbox run: fetch state → pipeline → execute-within-caps → log → notify.
Operating doctrine, model policy, and invariants are in CLAUDE.md; T1–T6 are
non-negotiable. Phase 3 (decision pipeline: multi-analyst steps, LLM/news/market
seams, real reasoning receipts) is done and merged (`b1b9ea0`) — read
`docs/PHASE3-REPORT.md` and `docs/decisions/0004-phase3-decision-pipeline.md`
before touching anything, especially the open threads Phase 3 flagged for this
phase.

## Step 0 — Open-questions gate (answer before writing code)

1. **E*Trade OAuth renewal for an unattended daily run.** ADR-0002 point 1
   (Phase 1) chose "interactive daily OAuth dance (no unattended renewal)" —
   a human re-authenticates each day. SPEC §10 still lists "OAuth token
   renewal approach" as an open question. Phase 4's whole point is a
   launchd-scheduled run with **no human present** — if the E*Trade access
   token has expired (2 hr idle / nightly expiry, per SPEC §1) and nothing
   renews it, every unattended run fails at the first API call. This is a
   real blocker, not a nice-to-have: decide whether Phase 4 builds the
   automated renewal flow now, or whether the pilot's "once daily at market
   open" cadence can be made to work within a token lifetime some other way
   (e.g. a renewal step as part of the run itself, if E*Trade's OAuth 1.0a
   flow allows non-interactive renewal within the idle window — check the
   real constraint before assuming). Propose, confirm with Rishi, ADR.

2. **How does the Python-orchestrated runner reach the safety-gated
   `preview_order`/`place_order` path?** Evidence worth starting from: the
   already-scaffolded `launchd/com.rishi.trading-agent.decision-run.plist.template`
   (ADR-0001) targets `ProgramArguments = uv run python -m etrade_agent.runner`
   — a Python module entrypoint, not a `claude -p` shell invocation. That's
   consistent with ADR-0004 point 2's decision that pipeline steps run as
   Python-orchestrated `claude -p` subprocess calls via the injected
   `LLMClient` seam (`runner/llm_client.py::ClaudeLLMClient`, built in Phase
   3), not as one big agentic session where an LLM does all the reasoning
   *and* tool-calling itself. So the runner is a Python process that
   constructs a live pipeline and gets `Decision` objects back — but SPEC
   §3.1's module-import table currently lists `runner/`'s allowed imports as
   only `config, logs, notify`, not `server`, `etrade`, or `pipeline`. Two
   shapes to decide between:
   - **(a) Direct in-process calls** — `runner/` imports
     `server.tools`/`etrade.client`/`store.state` directly and calls the
     plain functions (`preview_order`, `place_order`) in the same process as
     the pipeline. Simplest, fewest moving parts; `ConfiguredSafetyGate`
     still runs (T1 isn't weakened), but the runner/MCP-server process
     boundary SPEC §3's architecture diagram draws collapses into one
     process. Requires an ADR amending §3.1's import table.
   - **(b) MCP client over stdio** — `runner/` launches (or connects to) the
     etrade MCP server as a genuine MCP client, calling tools the same way a
     live `claude -p` agent would. Preserves the process boundary and the
     "reached only via the tool-call protocol" framing literally, but adds a
     new dependency (an MCP client library/session) for one daily run.

   Propose, confirm, ADR (batch with #1). This determines almost everything
   else about this phase's shape — resolve it first.

3. **Does Phase 4 close the `realized_pnl` dependency, or defer it again?**
   `docs/PHASE3-REPORT.md`/ADR-0003 point 9 explicitly framed automatic
   `realized_pnl` population as "coupled to execution, not to [Phase 3's]
   proposal logic" — meaning Phase 4, which is where execution becomes real,
   is exactly where this dependency was pointed at. Decide whether this
   phase wires it (matching closed positions against cost basis across
   trades as fills happen) or explicitly re-defers with justification (e.g.
   to a dedicated position-tracking phase, if one gets added) — either is
   fine, but per Phase 2/3's own discipline, don't let it silently vanish
   from the open-thread list a third time. Propose, confirm, ADR (batch with
   #1–#2).

4. **Verify `claude` + Max-subscription auth actually work under launchd's
   minimal environment — before writing the schedule around it.** SPEC §9
   says this plainly: "`claude` availability + Max OAuth under launchd must
   be verified before Phase 4 trusts the schedule." This is a distinct
   concern from #1 (E*Trade's OAuth, not Claude's) and is a verification
   task, not a design proposal — but do it *early*: if headless `claude`
   doesn't authenticate cleanly under launchd's stripped-down `PATH`/env
   (no login shell, no interactive terminal), that's new information that
   could force a rethink of #2's answer, not something to discover after
   the rest of the phase is built on top of an assumption.

## Context to load (context diet — nothing more)

- CLAUDE.md (invariants + doctrine), `docs/PHASE3-REPORT.md`,
  `docs/decisions/0004-phase3-decision-pipeline.md` (Phase 3's pipeline
  contracts and the injected-seam decision this phase wires up for real)
- SPEC §9 in full (operations: scheduling/cadence, notifications, logging,
  headless invocation — the `runner/headless.py` framing this phase makes
  real), §7 Phase 4 row + run wall, §3 (architecture diagram — the
  Claude-Code/MCP-server process split Step 0 #2 needs to reconcile with
  reality), §3.1 (module map — the import-table amendment Step 0 #2 likely
  needs), §10 (open questions — OAuth renewal, still open)
- `docs/decisions/0002-phase1-oauth-and-server-wiring.md` point 1
  (interactive daily OAuth — what Step 0 #1 revisits)
- Current stubs: `runner/headless.py::run_agent` (real, from Phase 1/3 — the
  "decision-run entrypoint" docstring already anticipates this phase),
  `scripts/generate_plist.py::main` (`raise NotImplementedError("Phase 4")`
  — implement for real), `launchd/*.plist.template` (already scaffolded,
  ADR-0001 — read before redesigning). **`runner/status.py` is explicitly
  Phase 5's** (`raise NotImplementedError("Phase 5")` in its own docstring)
  — SPEC §9 names status reports near this phase's other deliverables but
  don't build it here; that's next phase's job, not this one's.
- Skills that will fire: adr-writing (Step 0's batched decisions),
  spec-compliance (T1/T2 — whichever Step 0 #2 shape is chosen must not
  create a shortcut around the gate; T4 — real receipts flowing end-to-end
  for the first time), sandbox-prod (this phase places real, if sandbox,
  orders for the first time via an unattended path — read it before the
  first live run, not after), safety-wall (the run wall)

## Deliverables (SPEC §7 Phase 4 row + §9)

1. **`runner/headless.py` in anger** (or a new orchestration module, per
   Step 0 #2's answer) — the real `fetch state → pipeline → execute within
   caps → log → notify` loop, replacing every fake/mock this phase's own
   tests use.
2. **`runner/__main__.py`** — the `python -m etrade_agent.runner`
   entrypoint the plist template already targets; doesn't exist yet.
3. **Wire a live pipeline instance** — `ClaudeLLMClient` +
   `WebSearchNewsSource` + a live `MarketDataSource` (structurally satisfied
   by `EtradeClient`, per `pipeline/market.py`), carried directly from
   `PHASE3-REPORT.md`'s "Left for Phase 4."
4. **`Decision` → order mapping** — BUY/SELL decisions become order
   attempts through the already-built `preview_order`/`place_order` receipt
   seam (ADR-0004), carrying the real `reasoning_summary`/`signals_json`.
   HOLD decisions produce no order — make this explicit and tested, not
   incidental.
5. **`scripts/generate_plist.py::main`** — implement for real: render the
   template, write the plist, print the `launchctl load` command (its own
   docstring already specifies this).
6. **Notifications for this phase's new events** — trade executed (with
   reasoning summary), run summary/errors — via the existing
   `notify/ntfy.py` (Phase 2), no new notification mechanism needed.
7. **Run wall** — see below.

## Run wall (SPEC §7 Phase 4 row)

"End-to-end sandbox run executes ≤ caps and writes complete receipts."
Deterministic — fake `LLMClient`/`NewsSource` (reuse the Phase 3 wall's
pattern; new fixtures under `fixtures/pipeline/` or `fixtures/run/` as
needed), but consider using the **real** `ConfiguredSafetyGate` (not a fake)
against a fake/fixture-backed `EtradeClient`, so "executes ≤ caps" is
actually proven against real gate logic end-to-end, not asserted against a
stand-in. Mirror the `phase1`/`phase3` precedent: `tests/wall/phase4/`, a
new `phase4` marker, its own `conftest.py`, its own CI job
(`continue-on-error: true` while open), and extend `ci.yml`'s `safety-wall`
job scope to exclude `phase4` too, in the *same* commit that introduces the
marker (don't let a still-red phase4 wall test leak into the day-one-
blocking caps wall, the mistake Phase 3 deliberately avoided).

## Standing warnings

- **Real E*Trade sandbox orders get placed for real, unattended, starting
  this phase.** Even though it's sandbox, this is qualitatively different
  from Phases 1–3 (hand-tested or fully faked) — walk the sandbox-prod
  skill before the first live end-to-end run, and verify kill-switch state
  first (SPEC §4.3: ships engaged on a fresh DB, but this repo's DB isn't
  fresh anymore — check its actual current state, don't assume).
- **T1 still means `server/safety.py` is the only enforcement**, regardless
  of which Step 0 #2 shape is chosen. If the runner calls `server.tools`
  functions directly (shape (a)), it must still go through
  `ConfiguredSafetyGate` — no code path may construct an order and reach
  `EtradeClient.place_from_binding` without `check_place` running first.
- **HOLD decisions and decisions for non-whitelisted/malformed symbols must
  never silently become order attempts.** The `Decision`→`OrderRequest`
  mapping step (deliverable #4) is exactly where a bug here would hide.
- **Carried-forward open threads, explicitly re-flagged, not silently
  dropped:** `renew_tokens()` still unwired (Phase 1; may resolve via Step 0
  #1 or may not — say which), malformed tool input can still surface a raw
  `ToolError` (Phase 1/2), `scripts/remote_listener.py` unscheduled (Phase
  2), phone control needs `.env` provisioning (Phase 2), `positions_cache`
  unpopulated (Phase 2, deliberate), sandbox canned-data limitations (Phase
  1), and — new from Phase 3's review — advisory risk notes
  (`context.notes["risk_advisories"]`/`["risk_advisory_llm"]`) have no
  durable storage path; this phase's "log" step is exactly where that gets
  resolved or explicitly deferred again with reasoning, not silently
  dropped a second time.
- **Real cap numbers / pilot capital** — still open per SPEC §10. This phase
  builds and tests the orchestration mechanism against
  `tests/conftest.py::VALID_CONFIG_TOML`, same as every prior phase; going
  live with real money is Phase 6, not this one.
- Full gates once before push: `uv run ruff check . && uv run ruff format --check .
  && uv run mypy && uv run pytest` plus the wall run
  (`uv run pytest -m wall --override-ini "addopts="`, must still include the
  caps wall, Phase 1 fixture wall, and Phase 3 pipeline wall green
  regardless of where the run wall lands).
- Close the phase with a short `docs/PHASE4-REPORT.md` post-mortem (same
  shape as Phases 1–3's), noting what's left for Phase 5 (observability:
  `runner/status.py` for real, daily digest) and Phase 6 (pilot cutover).
