# Phase 2 Kickoff Prompt — Safety Layer

Paste this to start a Phase 2 session. Model: `opusplan` (pinned in `.claude/settings.json`).

---

You are implementing **Phase 2 of SPEC §7** for the etrade-agent repo: the full safety
layer — every §4.2 gate for real, the SQLite store, and manual reset/kill CLIs.
Operating doctrine, model policy, and invariants are in CLAUDE.md; T1–T6 are
non-negotiable. Phase 1 (OAuth, client, six MCP tools, fixture wall) is done and
merged — read `docs/PHASE1-REPORT.md` and `docs/decisions/0002-phase1-oauth-and-server-wiring.md`
before touching anything, especially the open threads it flags for this phase.

## Step 0 — Open-questions gate (answer before writing code)

1. **Gate evaluation order.** SPEC §4.2's table lists ten gates with different
   check points (`startup` / `preview` / `place`). `check_place` must run
   kill-switch first (already documented in `SafetyGate`'s docstring) — but the
   full order for the rest (capital-ceiling, per-trade-cap, daily-trade-limit,
   loss-breaker, whitelist, policy-long-only, policy-security-type,
   preview-required) isn't decided. Propose an explicit order, confirm with
   Rishi, ADR it (which refusal reason surfaces first when multiple gates would
   fire matters for T4 auditability and for what an operator sees).
2. **"Today" for `caps_state`.** The schema keys `caps_state` by `date_utc`
   (SPEC §5.1). The pilot runs once daily at market open (SPEC §9) — decide
   whether "today" is the UTC calendar day or a market-session day, and whether
   that decision could ever put a trade in the wrong day's bucket around
   midnight UTC. Propose, confirm, ADR (batch with #1).
3. **Unrealized P&L for the loss-breaker.** SPEC §4.2's `loss-breaker` gate
   trips on "realized+unrealized daily P&L ≤ −daily_loss_pct%." Realized P&L
   is derivable from `trade_log`; unrealized needs current market value vs.
   cost basis. Decide the source (a live `get_positions`/`get_quote` call at
   gate-check time, vs. `positions_cache` — SPEC §5.1 calls the cache
   "advisory," so treat it as such, not authoritative for a safety
   calculation). Propose, confirm, ADR (batch with #1–#2).
4. **Reset/kill CLI shape.** SPEC §4.3: breaker reset and kill-switch
   engage/disengage are "manual only," "requires the operator," "logged +
   notified." No script exists yet (`scripts/` currently has
   `oauth_login.py`, `record_fixture.py`, `generate_plist.py`). Decide: one
   script with subcommands, or two scripts (`scripts/reset_breaker.py`,
   `scripts/kill_switch.py`)? What does "requires the operator" mean
   mechanically — a confirmation prompt, an `--i-understand` flag, both?
   Propose, confirm, ADR (batch with #1–#3).

## Context to load (context diet — nothing more)

- CLAUDE.md (invariants + doctrine), `docs/PHASE1-REPORT.md`,
  `docs/decisions/0002-phase1-oauth-and-server-wiring.md` (Phase 1 open threads
  this phase inherits — richer decision storage, `ConfiguredSafetyGate` swap)
- SPEC §4 in full (safety-layer contract: §4.1 refusal shape, §4.2 gate table,
  §4.3 state transitions), §5.1 (SQLite schema — already real DDL in
  `store/schema.py`, migrations forward-only), §7 (Phase 2 row), §8 (config —
  caps/whitelist/policy structure, no-defaults rule)
- Current stubs (all raise `NotImplementedError("Phase 2 (SPEC §7)")`):
  `server/safety.py::ConfiguredSafetyGate` (the Protocol, `Refusal`, and
  Phase-1's `PassthroughGate`/`preview_required_refusal` are real — don't
  touch those, `PassthroughGate` gets swapped out, not deleted, until the cap
  wall forces the swap), `store/db.py::connect`/`apply_migrations`
- Skills that will fire: safety-wall (the cap wall is the whole point of this
  phase), spec-compliance, adr-writing

## Deliverables

1. `store/db.py` — SQLite connect (WAL mode) + forward-only migration runner
   against `store/schema.py::MIGRATIONS` (already written, don't redesign the
   schema without an ADR).
2. `server/safety.py::ConfiguredSafetyGate` — every SPEC §4.2 gate for real:
   `caps-required` (already enforced at startup via `load_config`/`AppConfig`,
   Phase 1 — confirm it still holds, don't reimplement), `kill-switch`,
   `capital-ceiling`, `per-trade-cap`, `daily-trade-limit`, `loss-breaker`,
   `whitelist`, `policy-long-only`, `policy-security-type`. `preview-required`
   is already implemented (Phase 1, T2) — leave it alone. Every gate refuses
   with the exact SPEC §4.1 shape via `Refusal.to_payload()` (never raised —
   see ADR-0002 point 7 for why raising corrupts it through FastMCP).
3. **Swap the gate in `server/app.py::create_app`**: `PassthroughGate()` →
   `ConfiguredSafetyGate(config)`. This is the actual moment Phase 1's
   documented safety net (sandbox-only + no-op gate) ends — treat it as the
   most consequential single line of this phase.
4. Manual reset/kill CLI (`scripts/`, shape decided in Step 0) — writes
   `caps_state`/`kill_switch` rows, requires the operator, logged (`logs.py`)
   + notified (`notify/ntfy.py` — check whether `NTFY_TOPIC` is set; Phase 1
   left it empty, this may be this phase's problem to raise, not solve).
5. **Cap wall** (`tests/wall/` — NOT a `phase2` subdirectory; SPEC §7 calls
   this the direct continuation of the bootstrap caps wall that's been
   blocking since day one, per `ci.yml`'s own comment "grows the full
   try-to-violate-every-cap suite in Phase 2"). One test per §4.2 gate that
   attempts the violation and asserts the exact §4.1 refusal shape. Committed
   red first (safety-wall skill) — the wall is the acceptance bar, written
   before the gate logic that must satisfy it.
6. Every executed trade writes a `trade_log` row with `reasoning_summary`,
   `signals_json`, `caps_snapshot_json` (T4) — even though there's no real
   pipeline yet (Phase 3), the *mechanism* for writing these receipts on a
   successful `place_order` is this phase's job; a placeholder
   reasoning_summary is fine, an empty/missing column is not.

## Standing warnings

- **Walls are never weakened to pass** (safety-wall skill). A gate test
  failing means the gate is wrong, not the test — this phase's wall is the
  spec's acceptance bar for whether real money is safe to risk.
- **No cap defaults, anywhere** (T5) — pydantic models, function signatures,
  test helpers, `or`-fallbacks. `tests/conftest.py`'s `VALID_CONFIG_TOML`
  exists specifically so tests inject caps explicitly; that friction is
  intentional, don't build around it.
- **`PassthroughGate` stays in the tree** (it's Phase 1's documented,
  ADR'd artifact) but must not be reachable from `create_app` once this phase
  closes — the spec-compliance checklist's T1 item should catch this, don't
  skip it.
- Real cap numbers / pilot capital are still Rishi's open decision (SPEC §10)
  — this phase builds the mechanism with synthetic test values, same as
  Phase 1; it does not require or wait on the real numbers.
- Sandbox only, still (sandbox-prod skill) — this phase adds no new prod
  surface, but the loss-breaker/kill-switch logic is exactly the kind of code
  a future session might be tempted to "just quickly test against prod" —
  don't.
- Full gates once before push: `uv run ruff check . && uv run ruff format --check .
  && uv run mypy && uv run pytest` plus the wall run
  (`uv run pytest -m wall --override-ini "addopts="`, which now includes both
  the caps wall and Phase 1's fixture wall — both must stay green).
- Close the phase with a short `docs/PHASE2-REPORT.md` post-mortem (same
  shape as Phase 1's), noting what's left for Phase 3 (pipeline shape spike).
