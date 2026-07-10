# 0002 — Phase 1 OAuth, preview binding, and server wiring

**Date:** 2026-07-10
**Status:** accepted

## Context

Phase 1 (SPEC §7) implements the E*Trade OAuth 1.0a dance, a typed REST client, and
the six MCP tools, hand-verified against the live sandbox. Several judgment calls
came up while building and verifying against real E*Trade behavior — batched here
per the adr-writing skill rather than as micro-ADRs. A few were resolved by facts
discovered only by hitting the live sandbox (E*Trade's public docs and the shape
assumed at design time didn't match reality in three places).

## Decision

**1. Token renewal — interactive dance per run.** `scripts/oauth_login.py` runs the
full request→browser-authorize(`oob`)→access dance; the operator pastes the
verifier; tokens persist to gitignored `tokens/`. `renew_tokens()` is wired for
intra-day idle recovery (E*Trade's 2 hr idle timeout) with no browser step, but
cannot survive the midnight-ET hard expiry — a fresh dance is required every
morning regardless. Unattended (launchd) auth is an explicit **Phase 4** open
problem: E*Trade's oob flow needs a human for the verifier code, so a
once-daily-at-open unattended run is not solved by this phase. Confirmed
acceptable with Rishi (~30s daily step).

**2. Preview→place binding — in-memory per server process (T2).** `PreviewStore`
(`server/preview_store.py`) is a plain dict inside the running MCP server; its
lifetime IS "the same run." Entries are one-shot — `consume()`d after a successful
place, so a preview can't be replayed. A restart wipes all bindings, so a place can
never reference a preview from a different run — this strengthens T2 rather than
weakening it. The durable *decision receipt* (`trade_log`, Phase 2, T4) is a
separate layer and isn't sacrificed by this choice. Rishi's request for richer
decision-history capture (for iterating on the system over time) is carried as a
Phase 2/3 `store/` schema design thread, not folded into this ephemeral binding.

**3. OAuth host is shared infrastructure, not a prod-path violation.** E*Trade's
`api.etrade.com/oauth/*` and `us.etrade.com/e/t/etws/authorize` are the *only*
endpoints for the OAuth dance — there is no `apisb` OAuth host. Sandbox-ness is
selected by the **sandbox consumer key**, not the OAuth URL. These endpoints mint
tokens; they are not the money path. The "no prod URL this phase" rule (SPEC §7,
sandbox-prod skill) applies to the **data/order base**
(`SANDBOX_BASE_URL = apisb.etrade.com`), which `EtradeClient` always uses this
phase — `PROD_BASE_URL` is a defined-but-never-passed constant.
`etrade/oauth.py`'s `OAUTH_BASE_URL`/`AUTHORIZE_URL` are named and commented so a
future reader doesn't "fix" them to a nonexistent sandbox host.

**4. Phase-1 `PassthroughGate` — a labeled, non-enforcing safety gate.**
`ConfiguredSafetyGate.check_preview/check_place` are Phase 2 work
(`NotImplementedError`). SPEC §7 Phase 1 requires all six tools — including
`preview_order`→`place_order` — hand-tested against sandbox before any autonomous
loop exists. `server/safety.py::PassthroughGate` satisfies the `SafetyGate`
Protocol with both checks returning `None` (allow). The tool call sites (T1) are
wired regardless — only the gate's *decision* is a no-op. Safety net while in use:
(a) it is clearly labeled Phase-1-only in its docstring; (b) `create_app` hard-
refuses to start outside `environment.mode == "sandbox"`; (c) Phase 2's cap wall
forces the swap to `ConfiguredSafetyGate` before caps are considered live.

**5. `OrderPreview.estimated_cost` is computed client-side.** Verified live: E*Trade's
preview response has **no total-cost field** — only `estimatedCommission`/
`estimatedFees`. Since this value is exactly what Phase 2's `capital-ceiling`/
`per-trade-cap` gates will check, a wrong or near-zero value would silently defeat
those gates once built. `EtradeClient.preview_order` computes
`estimated_cost = quantity * price_basis + commission + fees`, where `price_basis`
is `order.limit_price` for LIMIT orders (the order's own worst-case boundary — no
extra call needed) or a fresh `get_quote(symbol).last` for MARKET orders (fetched
via one extra call, since fill price is unknown pre-trade). `quantity` always comes
from the caller's own `OrderRequest`, never the response's echoed value — E*Trade's
sandbox returns fully canned data (fixed fake symbol/quantity/previewId regardless
of the real request), confirmed live and consistent with the etrade-fixtures
skill's documented sandbox caveat.

**6. Account resolution: `accountMode == "IRA"` is the real retirement signal.**
Verified live: an IRA account can report `accountType == "MARGIN"` (not
`"INDIVIDUAL_RETIREMENT"`) — a naive `accountType`-only filter wrongly included it.
`EtradeClient._select_brokerage_account` filters on `accountStatus == "ACTIVE" and
accountMode != "IRA"`. `accountIdKey` (T3-sensitive, account-identifying) is
resolved via `/v1/accounts/list` at `EtradeClient.connect()` time and is never
written to `config.toml` or fixtures; it lives in `.env ETRADE_ACCOUNT_ID_KEY`
(gitignored) when explicit selection is needed — this sandbox account genuinely
has two indistinguishable active CASH/INDIVIDUAL accounts, so auto-resolution
correctly refuses (fail-closed) and Rishi selected one explicitly.

**7. Refusal payloads are returned, never raised as `ToolError`.** Verified live
against the installed `mcp` package: raising `ToolError` inside a `@app.tool()`
function gets caught and re-wrapped as
`f"Error executing tool {name}: {original_message}"` — this corrupts the exact
SPEC §4.1 `{"refused": true, "gate", "reason", "state"}` shape into free text.
`server/tools.py`'s `preview_order`/`place_order` handlers `return
refusal.to_payload()` as the tool's normal result instead, so the parsed contract
survives intact (server/CLAUDE.md: "a parsed contract, not a message").

**8. Fixture scrubbing is by-value AND structural, not just by-key.** An
account-identifying value leaked into this session's own working transcript
during live-shape probing, appearing both as a plain field (`accountId`) AND
embedded inside unrelated URL strings (`lotsDetails`/`quoteDetails`/`details`)
that a key-name-only scrubber missed. Root-caused in two layers:

- First fix: `scrub_fixture` collects every value found under a sensitive key
  name (`accountId`, `accountIdKey`, `accountKey`) anywhere in the payload,
  unions it with known secrets passed in explicitly (consumer key/secret,
  access token/secret, and — critically — `EtradeClient.account_id_key`, the
  actually-in-use key, whether explicit or auto-resolved; the first version of
  `record_fixture.py::main()` omitted this and re-leaked the same class of
  value into two fixtures before this was caught), then masks every string
  occurrence of any of those values recursively.
- Second fix, after that still didn't fully close the gap: the specific value
  that leaked turned out to be **E*Trade's own hardcoded canned/demo constant**
  embedded in its sandbox `lotsDetails`/`details` example URLs — confirmed by
  direct comparison, it does not match Rishi's real `accountIdKey` at all (same
  length, different value; consistent with the sandbox's other canned data —
  fixed symbol "IBM"/"GOOG", fixed `previewId`, fixed order ids — documented in
  the etrade-fixtures skill's sandbox caveat). A value-based scrubber can never
  catch an id it has no way to know in advance. `scrub_fixture` therefore also
  applies a structural regex (`/accounts/<id>/` in any string) that masks any
  account-shaped URL path segment regardless of whether the id is a "known"
  sensitive value.

Both fixes are covered by tests (`tests/scripts/test_record_fixture.py`); all
six fixtures were re-recorded and verified fixture-clean (both by known-value
grep and by structural pattern) before committing (T3).

**9. Phase 1 close: `phase1-wall` CI job flipped to blocking.** A whole-branch
review (superpowers:requesting-code-review, per CLAUDE.md's Review Policy) ran
before close and found one Important issue — `_select_brokerage_account`'s
ambiguous-account refusal embedded raw `accountId` values in its message
(contradicting the plan's explicit "redacted list" requirement), and
`create_app`/`main` didn't catch that `ValueError`, so it would have propagated
as an unhandled traceback (with those account ids) into `launchd`'s stderr log
in Phase 4. Fixed via TDD: the message now reports counts only, and
`create_app` catches it and fails closed as a `ServerStartupError`, matching
every other startup check. Three Minor findings (the module docstring
overclaiming "never raised" for malformed tool input; `renew_tokens` being
tested but not yet wired into any runtime path; `scripts/record_fixture.py`
being a second, sanctioned caller of `EtradeClient.place_from_binding`) are
carried as open threads in `docs/PHASE1-REPORT.md` rather than blocking close —
none violate an invariant, and the reviewer's own recommendation was to defer
them. With the Important fix verified (full suite + both wall splits green,
ruff/mypy clean), `.github/workflows/ci.yml`'s `phase1-wall` job had its
`continue-on-error: true` removed — it is now, like `safety-wall`, a required
merge gate.

## Alternatives

- Automated/unattended OAuth renewal now — rejected; E*Trade's oob flow
  structurally requires a human browser step, and building around that (e.g.
  storing E*Trade web login credentials) is explicitly out of scope and risky.
- SQLite-persisted preview→place binding — rejected; weakens T2's "same run"
  guarantee by letting a stale preview survive process restarts into a later run.
- Trusting a nonexistent `estimatedTotalAmount` field for `estimated_cost` —
  rejected after discovering it doesn't exist; would have shipped a near-zero
  cost estimate that silently defeats Phase 2's capital gates.
- Filtering eligible accounts on `accountType` alone — rejected after live testing
  showed an IRA account reporting `accountType == "MARGIN"`.
- Raising `ToolError(json.dumps(refusal.to_payload()))` — rejected after
  confirming live that FastMCP wraps/prefixes the message, corrupting the shape.
- Key-name-only fixture scrubbing — rejected after it let a real account key
  leak via an embedded URL field during this session's own live testing.

## Consequences

- A daily manual OAuth step is required through at least Phase 4; unattended
  scheduling (SPEC §9, launchd) inherits this as an explicitly open problem to
  solve before Phase 4 trusts the schedule.
- `PreviewStore` bindings do not survive a server restart — a place attempted
  after a restart always refuses with `preview-required`, by design.
- Phase 2's `ConfiguredSafetyGate` must be swapped in before any cap is
  considered enforced; `PassthroughGate` remaining wired past Phase 2 would be a
  spec-compliance violation to catch via the T1 checklist.
- `estimated_cost`'s MARKET-order path costs one extra `get_quote` call per
  preview; acceptable at pilot scale (one decision run/day).
- Richer decision-history capture (beyond `trade_log`'s baseline) is deferred to
  a Phase 2/3 `store/` schema ADR — tracked in `docs/PHASE1-REPORT.md`.
