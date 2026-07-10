# Phase 1 Report — E*Trade MCP Server Foundation

**Date:** 2026-07-10
**Status:** closed

## What shipped

Per SPEC §7 Phase 1's deliverable list, all six items:

1. **OAuth 1.0a dance + renewal** (`etrade/oauth.py`, `scripts/oauth_login.py`) —
   request→browser-authorize(`oob`)→access token, HMAC-SHA1 via requests-oauthlib.
   `renew_tokens()` implemented and unit-tested (intra-day idle recovery path) —
   see Open Threads below on wiring it into a runtime call site.
2. **Six MCP tools against sandbox** (`server/tools.py`, `server/app.py`,
   `etrade/client.py`) — `get_quote`, `get_positions`, `get_balances`,
   `preview_order`, `place_order`, `get_order_status`, all hand-verified live.
3. **Fixtures + replay tests** — one scrubbed fixture per endpoint recorded from
   the real sandbox (`fixtures/etrade/`), replay-tested against pydantic models
   (`tests/etrade/test_parsers.py`) and against the recorded fixtures themselves
   (the Phase 1 wall, below).
4. **Hand-tested interactively** — all six tools verified end-to-end through a
   real MCP client (stdio JSON-RPC transport) against a running
   `uv run python -m etrade_agent.server.app` process, exercising the identical
   code path `.mcp.json` configures. Confirmed: quote retrieval, balances,
   positions, a real preview→place→status sequence, and the T2 refusal path
   (unknown `preview_id` refuses without reaching E*Trade).
5. **Phase 1 wall** — `tests/wall/phase1/test_fixture_wall.py`, isolated from
   the day-one-blocking caps wall via a `phase1` pytest marker (own conftest,
   registered marker, split CI jobs). Deliberately verified to have real teeth:
   a fixture field was temporarily corrupted mid-development and confirmed to
   fail the wall for the right reason, then restored. Flipped to a blocking CI
   job (`continue-on-error` removed) at this close, per ADR-0002 point 9.
6. **ADR-0002** — batches every Phase 1 judgment call (token renewal approach,
   in-memory preview binding, OAuth host reality, the Phase-1 `PassthroughGate`,
   client-side `estimated_cost` computation, IRA account detection, refusal
   delivery mechanism, fixture scrubbing, and the phase-close wall flip).

**Gates at close:** 87 unit tests, 7 Phase 1 wall tests, 16 caps wall tests —
all passing. `ruff check`, `ruff format --check`, `mypy --strict` — all clean.
One whole-branch code review ran before close (superpowers:requesting-code-review);
its Important finding was fixed and verified (ADR-0002 point 9).

## What drifted from spec (and why)

- **`OrderPreview.estimated_cost` required a design not specified anywhere.**
  E*Trade's real preview response has no total-cost field at all (only
  `estimatedCommission`/`estimatedFees`) — discovered live, not documented
  anywhere in E*Trade's public docs consulted during planning. Resolved by
  computing it client-side from the order's own `limit_price` or a fresh quote
  (ADR-0002 point 5). This is exactly the kind of value Phase 2's
  `capital-ceiling`/`per-trade-cap` gates will trust — getting it right now
  mattered more than staying inside the original plan's scope.
- **Account resolution needed a live decision from Rishi.** The sandbox account
  genuinely has two indistinguishable active CASH/INDIVIDUAL accounts;
  auto-resolution correctly refuses rather than guessing (T5-adjacent
  discipline applied to account selection, not just caps). Rishi picked one
  interactively; `ETRADE_ACCOUNT_ID_KEY` now lives in `.env`.
- **Refusal delivery mechanism changed mid-implementation.** The plan assumed
  raising `ToolError` with the SPEC §4.1 JSON payload as the message would
  work; verified live against the installed `mcp` package that FastMCP wraps
  and prefixes any raised message ("Error executing tool X: ..."), corrupting
  the shape. Tool handlers return the refusal payload as a normal result
  instead (ADR-0002 point 7).
- **Fixture scrubbing needed two rounds.** A key-name-only scrubber missed an
  account-identifying value embedded inside URL fields (`lotsDetails`/`details`)
  — caught by the auto-mode classifier when it nearly landed in a test file,
  then found again (a different, E*Trade-canned value) via direct fixture
  inspection during the pre-commit T3 checklist walk. Both gaps are closed with
  tests (ADR-0002 point 8). This is the incident worth remembering: value-based
  scrubbing alone is insufficient when the sensitive value can't be known in
  advance — structural pattern matching (any `/accounts/<id>/` URL segment) is
  the durable fix, not an ever-growing known-values list.
- **The throwaway `config/config.toml` exists locally**, gitignored, clearly
  labeled as non-canonical, created only to hand-test the MCP server before
  real caps/pilot capital are decided (SPEC §10). Not a spec deviation — SPEC
  §10 explicitly defers that decision — but worth flagging so nobody mistakes
  it for the real pilot config later.

## Open threads for Phase 2 (and beyond)

- **Richer decision-history storage.** Rishi wants to iterate on the system
  over time and asked about durable decision storage. `trade_log` (Phase 2,
  T4: `reasoning_summary`, `signals_json`, `caps_snapshot_json`) covers the
  baseline. Design *richer* capture — rejected alternatives, market snapshot at
  decision time, signal confidence, backtest/what-if data — deliberately in the
  Phase 2/3 `store/` schema, with its own ADR. Explicitly not folded into the
  Phase 1 preview→place binding (`PreviewStore`), which is a different,
  intentionally ephemeral thing (ADR-0002 point 2).
- **`ConfiguredSafetyGate` must replace `PassthroughGate`.** Phase 1's
  `PassthroughGate` enforces nothing — sandbox-only startup and this phase's
  own limited scope are the only safety net. The cap wall (Phase 2) is what
  forces this swap; until then, no code path in this repo should be treated as
  actually enforcing caps.
- **`renew_tokens()` isn't wired into any runtime call site yet** (code-review
  finding, Minor). It's implemented and unit-tested but neither `create_app`
  nor `EtradeClient` calls it on a 401/idle response today — an idle-expired
  session currently surfaces as a raw `HTTPError`. Not a Phase 1 gap (SPEC §7
  only required hand-testing this phase), but should be wired before Phase 4's
  unattended runs need automatic intra-day recovery.
- **Malformed tool input can still surface a raw `ToolError`.** (code-review
  finding, Minor.) `preview_order`'s FastMCP wrapper constructs `OrderAction`/
  `OrderType`/`SecurityType` enums from raw strings before the business
  function or gate ever runs; a bad value raises an uncaught `ValueError`,
  wrapped by FastMCP into the same message-corrupting shape ADR-0002 point 7
  exists to avoid — just triggered by malformed input rather than a gate
  refusal. Low real-world risk (a well-formed orchestrator won't send garbage
  enums) but worth tightening — either catch construction errors and return a
  `Refusal`-shaped response, or narrow the module docstring's claim.
- **`scripts/record_fixture.py` is a second, sanctioned caller of
  `EtradeClient.place_from_binding`.** (code-review finding, Minor — a
  documentation-precision item, not a bug.) It bypasses `PreviewStore`/
  `SafetyGate` entirely to record the `place_order` fixture, which is correct
  for a human-invoked, sandbox-only recording utility (SPEC §5.4) — but any
  future claim of "`place_from_binding` has exactly one caller" should say
  "...one caller in the `place_order` *tool* path" to stay accurate.
- **OAuth token renewal remains a daily manual step** through at least Phase 4
  (ADR-0002 point 1). Unattended (launchd) auth is an explicitly open problem —
  E*Trade's oob flow structurally requires a human browser step. SPEC §9
  already flags "Max-OAuth under launchd must be verified before Phase 4
  trusts the schedule"; this carries that forward with a concrete reason why.
- **Real cap numbers + pilot capital** — still open per SPEC §10, decided when
  Rishi funds the account. The throwaway `config/config.toml` must be replaced
  entirely before any real trading.
- **Sandbox canned-data limitations** worth remembering for Phase 6 prod
  cutover: quotes always return a fixed symbol/price regardless of the
  requested one; the `/orders` list endpoint returns a fixed demo set
  regardless of what was actually just placed (confirmed live: a freshly
  placed order's id does not appear in a subsequent `get_order_status` lookup
  against the canned list). None of this is a code defect — it's why the
  etrade-fixtures skill requires schema-drift tests to re-run against
  production before the first real order.
