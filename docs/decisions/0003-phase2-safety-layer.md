# 0003 — Phase 2 safety layer: gate order, day boundary, breaker P&L source, reset/kill CLI shape, remote triggering

**Date:** 2026-07-10
**Status:** accepted

## Context

Phase 2 (SPEC §7) implements every §4.2 gate for real, replacing the non-enforcing
Phase-1 `PassthroughGate` (ADR-0002 point 4) with `ConfiguredSafetyGate`. Several
judgment calls came up that the kickoff prompt explicitly required be proposed,
confirmed with Rishi, and ADR'd before writing gate logic — batched here per the
adr-writing skill, together with the internal design decisions needed to make the
gates buildable and testable.

## Decision

**1. Gate evaluation order at `check_place`: Halts → Legality → Sizing.**
`kill-switch` (mandated first, per `SafetyGate`'s existing docstring) →
`loss-breaker` → `daily-trade-limit` → `whitelist` → `policy-security-type` →
`policy-long-only` → `capital-ceiling` → `per-trade-cap` → `preview-required`
(already implemented, T2, and structurally first in `tools.py::place_order`
since it's a `PreviewStore` lookup, not a gate call). Rationale: when multiple
gates would refuse the same order, the operator should see the dominant
*operational* reason first — a day-level halt (something has already gone
wrong today) outranks an order that should never have been proposed (bad
symbol/side/type), which outranks an order that's merely oversized. This
ordering is what a human skimming `trade_log.refusal_gate` or the JSONL
refusal log will actually want to triage by. Confirmed with Rishi.

**2. "Today" for `caps_state` = UTC calendar day.** The pilot runs once daily
at US market open (SPEC §9), which falls at 13:30–14:30 UTC depending on DST —
always midday UTC, never within hours of the UTC midnight boundary. A UTC
calendar-day bucket (`date.today()` in UTC, matching the schema's existing
`strftime('%Y-%m-%dT%H:%M:%SZ', 'now')` timestamps) can therefore never put a
single run's trades in the wrong day's bucket, and needs no timezone/DST
handling anywhere in the caps-state read/write path. A market-session-day (ET)
bucket was considered and rejected — see Alternatives. Revisit only if the
cadence ever changes to overnight or continuous trading (currently a SPEC §1
non-goal).

**3. Loss-breaker unrealized P&L source = live `client.get_positions()`.**
The breaker gate computes `unrealized = Σ(position.market_value −
position.cost_basis)` over a live call at gate-check time, added to
`caps_state.realized_pnl` (today's realized P&L, populated as trades execute
— see point 6 on its current limitation). `positions_cache` (SPEC §5.1) is
explicitly "advisory... not authoritative"; using it for a safety-critical
breaker calculation risks tripping *late* on stale marks, which is the unsafe
direction for a circuit breaker. One extra E*Trade call per `check_place` is
negligible at pilot volume (one decision run/day, confirmed acceptable with
Rishi — no rate-limit concern raised).

**4. Reset/kill CLI shape: two scripts, typed confirmation + mandatory
`--operator`.** `scripts/reset_breaker.py` and `scripts/kill_switch.py`
(subcommands `engage`/`disengage`), matching the existing one-script-per-job
convention (`oauth_login.py`, `record_fixture.py`, `generate_plist.py`) rather
than one script with subcommands. "Requires the operator" (SPEC §4.3) is
enforced two ways: (a) an interactive typed confirmation — the operator must
type the action word back — which a `--yes` flag can skip for legitimate
scripted/remote use (see point 5); (b) a mandatory `--operator NAME` argument,
persisted into `kill_switch.changed_by` / `caps_state.breaker_reset_by`, so
the audit trail (T4-adjacent discipline applied to safety-state changes, not
just trades) always names who acted, even when `--yes` skips the prompt. Both
scripts load config via the existing `load_config`, write state via the new
`store/state.py` access layer (point 7), and log (`logs.log`) + notify
(`notify.ntfy.send`, no-op with a logged warning if `NTFY_TOPIC` is unset —
Phase 1 left it empty; this phase doesn't block on it being set, per the
kickoff prompt's framing of that gap as "this phase's problem to raise, not
solve").

**5. Phone control = full ntfy remote control (engage / disengage / reset),
amending SPEC §4.3.** Rishi requested the ability to resume/block trading
entirely from his phone — not merely receive alerts and fall back to a
terminal to act on them. Of the three shapes considered (SSH-only no-new-code;
ntfy one-tap halt-only; full ntfy remote control — see Alternatives), Rishi
chose full remote control after the safety trade-off was made explicit: engage
(halt) is fail-safe and low-risk to expose remotely, but disengage/reset
re-arm the money path, and SPEC §4.3's original text ("manual only... a CLI
action... requires the operator") was written assuming a human at a terminal,
which an ntfy push notification cannot satisfy — there is no interactive
typed-confirmation step over a push channel. **This decision amends SPEC
§4.3** (edited in this same commit) so that engage, disengage, and breaker
reset are each described as reachable through **two equally complete
channels** — a local CLI or a phone via ntfy — rather than a CLI-primary
mechanism with a remote add-on; the local scripts remain useful (offline,
scriptable, no ntfy dependency) but are not more authoritative than the phone
path. Both channels authenticate "requires the operator" the same way in
spirit — proof of deliberate human intent — but differ in mechanism: local
uses a typed confirmation + `--operator`; remote authenticates via a **TOTP
rotating code (RFC 6238)**, not a static token.

*Revised during the whole-branch review that closed this phase.* The initial
implementation authenticated remote commands with a static, reusable secret
token sent as the ntfy message body — the review caught that this token was
broadcast in cleartext over the very topic it authenticated: ntfy has no
per-subscriber confidentiality and caches messages for replay, so the first
legitimate use would have made the token permanently visible and replayable
to anyone who had found or guessed the topic, defeating the "requires the
operator" property the token was supposed to provide (Critical finding).
Presented with the fix options (a rotating code restricted to the two
dangerous actions only, a rotating code applied uniformly to all three
actions, a private second topic as a lower-assurance stopgap, or shipping
as-is with the flaw explicitly tracked for prod cutover), Rishi chose a
uniform TOTP requirement for engage, disengage, and reset-breaker alike —
simpler mental model (always read the current code from an authenticator
app) over minimizing friction on the fail-safe halt action specifically.
`etrade_agent/totp.py` implements RFC 6238 from the standard library only
(`hmac`/`hashlib`/`struct`/`time`/`base64`/`secrets`) — no new dependency —
and is verified against RFC 6238 Appendix B's own published test vectors, not
only self-consistency. `NTFY_COMMAND_SECRET` (`.env`) holds the shared
secret, provisioned once via `scripts/generate_totp_secret.py`, which prints
the secret and an `otpauth://` URI for a standard authenticator app (Google
Authenticator, Authy, 1Password, etc.) and is never invoked automatically —
the secret is meant to be seen exactly once, at setup. A captured code
expires within one ~30s rotation (default ±1-step verification window,
`verify_totp`) and cannot be usefully replayed, closing the gap the review
found. The topic string is still not treated as a secret credential for
authorization purposes (ntfy topics are guessable/discoverable by design;
`notify/ntfy.py`'s docstring was tightened to say so plainly) — TOTP is the
actual "requires the operator" proof for the remote path, not topic secrecy.
`NTFY_TOPIC` and `NTFY_COMMAND_SECRET` both live in `.env` (T3: neither in
code, logs, or fixtures — `logs.py`'s redaction list covers the renamed
variable). Every remote action is logged at the same severity as a local one
and triggers its own ntfy notification confirming the action taken, so a
compromised or mistaken remote trigger is immediately visible. The listener
(`scripts/remote_listener.py`) calls the identical `store/state.py` writers
the local CLIs use — there is no second, divergent enforcement path — and
records `changed_by="remote:ntfy"` / `breaker_reset_by="remote:ntfy"` so the
audit trail distinguishes remote from local actions without treating either
as more authoritative. Sandbox-only in this phase, same as the local CLIs.

## Internal design decisions

**6. `ConfiguredSafetyGate` gains two dependencies beyond `config`.**
Capital-ceiling, loss-breaker, and policy-long-only need live positions and
balances; kill-switch, daily-trade-limit, and loss-breaker need to read/write
`caps_state`/`kill_switch`. The constructor becomes
`ConfiguredSafetyGate(config: AppConfig, market: PositionsProvider, state:
StateStore)`. `PositionsProvider` is a new `Protocol` (`get_positions`,
`get_balances`) satisfied structurally by `EtradeClient` with no changes to
`etrade/client.py` — this keeps wall tests hermetic (inject a fake
`PositionsProvider` and a fake/temp-DB `StateStore`, no live E*Trade call in
the test suite) and keeps `server/CLAUDE.md`'s "trace the gate path first"
rule satisfiable without a network dependency. Per `server/CLAUDE.md`'s "fail
closed" rule, any exception or missing state inside a gate check is caught
and returned as a refusal, never allowed to propagate as an unhandled order.

**7. `check_preview` / `check_priced_preview` / `check_place` split.**
`OrderRequest` alone (available at `check_preview`) carries no
`estimated_cost` — that field exists only on `OrderPreview`, produced by
`client.preview_order()` (ADR-0002 point 5 computes it client-side because
E*Trade's response has no total-cost field). Rather than duplicate that
costing logic inside the gate to estimate a "good enough" pre-preview number
— which ADR-0002 point 5 already warns is exactly the kind of value that can
silently defeat these gates if wrong — the cost-dependent gates run once,
on the authoritative figure, immediately after pricing:
- `check_preview(order)`: whitelist, policy-security-type, policy-long-only —
  everything decidable from the request alone, refusing bad orders before
  any E*Trade call (T1).
- `check_priced_preview(preview, order)` (new): capital-ceiling,
  per-trade-cap — run in `tools.py::preview_order` immediately after
  `client.preview_order()` returns and before `store.put()`, so an
  oversized preview is refused and never becomes placeable at all.
- `check_place(preview, order)`: the full gate set again, kill-switch first,
  in the point-1 order — state can change between preview and place (another
  trade executes, the breaker trips, the kill switch engages), so SPEC
  §4.2's "preview + place" checkpoints for these gates means re-checking at
  place time, not trusting the preview-time answer.

**8. New `store/state.py` access layer.** `store/schema.py`'s DDL is fixed
(not redesigned here). `store/state.py` adds typed helpers used by the gates,
the CLIs, and the trade-log write path: `is_kill_engaged`/`set_kill_switch`,
`read_caps_state`/`increment_trades_executed`/`trip_breaker`/`reset_breaker`,
`write_trade_log`. This is the "state access" `store/` is documented to own
(SPEC §3.1 module map).

**9. `realized_pnl` starts at 0; loss-breaker runs on unrealized P&L alone
until Phase 3.** `caps_state.realized_pnl` is a column the gate reads, but
populating it automatically requires matching closed positions against cost
basis across trades, which depends on the Phase 3 pipeline's position
tracking (not built yet — Phase 2 has no pipeline, per SPEC §7). The
loss-breaker still functions correctly using live unrealized P&L alone (point
3), which is the dominant signal for a long-only, once-daily pilot. This is
tracked as a Phase 3 dependency in `docs/PHASE2-REPORT.md`, not silently
left blank.

**10. `trade_log` receipts are written for every refusal with a real
`OrderRequest`, not only successful places.** The kickoff prompt's framing of
Deliverable 6 ("the mechanism for writing these receipts on a successful
place_order is this phase's job") was read narrowly during initial
implementation as "successful only." A whole-branch review caught that this
directly contradicts SPEC §5.1's own text — `trade_log` is documented as "one
row per *attempted* order," with a nullable `refusal_gate` column that exists
specifically to record a refused attempt. Since `store/state.py::write_trade_log`
already fully supported the refusal shape (built and unit-tested from the
start, point 8), and the fix needed no schema change, `server/tools.py`'s
`preview_order` and `place_order` now call it on every gate refusal that has a
fully-specified `OrderRequest` to attach: `check_preview` refusals (no preview
priced yet — `preview_id`/`estimated_cost` are `NULL`), `check_priced_preview`
refusals (priced — both populated), and `check_place` refusals (both
populated, from the T2 binding). This required threading `state`/`config`/
`run_id` into `preview_order` (previously only `place_order` had them) —
`register_tools` passes the same three values it already built for
`place_order`. The one refusal NOT covered: T2's `preview-required` refusal
(`place_order` given an unknown `preview_id`) has no `OrderRequest` at all —
`PreviewStore.get()` found nothing — so there is no order data to write; it
remains JSONL-only, as it always was. Refusal rows never call
`increment_trades_executed` — a refused attempt didn't execute, so it must
not count toward `daily_trade_limit`.

## Alternatives

- **Point 1 — SPEC §4.2 table order verbatim** (kill-switch, capital-ceiling,
  per-trade-cap, daily-trade-limit, loss-breaker, whitelist,
  policy-long-only, policy-security-type): rejected — interleaves halt/
  legality/sizing reasons in a way that doesn't match how an operator would
  want to triage a refusal.
- **Point 1 — legality before halts** (whitelist/policy-* immediately after
  kill-switch, before loss-breaker/daily-limit): rejected — a tripped
  breaker is a more urgent signal than "this symbol isn't whitelisted," and
  should surface first even if both would independently refuse.
- **Point 2 — market-session day (America/New_York)**: rejected — adds
  timezone/DST conversion at every `caps_state` read/write for zero benefit
  at the once-at-open pilot cadence, and diverges from the schema's existing
  UTC timestamp convention.
- **Point 3 — `positions_cache` for unrealized P&L**: rejected — SPEC §5.1
  calls the cache advisory/non-authoritative; using stale marks for a safety
  breaker could let it under-trip.
- **Point 4 — one script with subcommands** (`scripts/safety.py kill
  disengage`): rejected — diverges from the established one-purpose-per-script
  convention with no offsetting benefit.
- **Point 4 — flag-only (`--i-understand`), no typed confirmation**:
  rejected as the sole local-CLI gate — a flag already present in a
  copy-pasted command provides materially less friction than an interactive
  prompt for the two most consequential actions in the system.
- **Point 5 — SSH-only, no new code**: the lowest-risk option (zero new
  attack surface, all local friction preserved) but rejected per Rishi's
  explicit request for phone-native control without an SSH client.
- **Point 5 — ntfy one-tap halt-only** (engage remote, disengage/reset stay
  local-only): the safety-conservative middle option — considered and
  presented, but rejected per Rishi's explicit choice of full remote control.
- **Point 5 (post-review revision) — TOTP restricted to disengage/reset
  only, static token kept for engage**: presented as the minimal-friction
  fix (replaying a captured "engage" is harmless — it only re-halts) but
  rejected per Rishi's explicit choice of a uniform requirement across all
  three actions.
- **Point 5 (post-review revision) — private second command topic, static
  token unchanged**: presented as a stopgap that raises the bar (an
  attacker needs to discover a second, unpublished topic) without touching
  the phone workflow — rejected because it doesn't close the underlying
  replay hole, only narrows who can reach it; once the private topic itself
  leaked (e.g. through the same class of exposure that could leak
  `NTFY_TOPIC`), the flaw would reappear identically.
- **Point 5 (post-review revision) — ship as-is, track for prod cutover**:
  the fastest path to closing Phase 2 — rejected because the flaw is
  architected to persist unchanged past this phase, and the fix was cheap
  and self-contained (stdlib-only) once identified; deferring it would only
  mean re-deriving the same fix later under more time pressure.
- **Point 6 — inject the whole `EtradeClient`** instead of a narrow
  `PositionsProvider` Protocol: rejected — would let the gate reach
  `preview_order`/`place_from_binding`, re-opening exactly the kind of
  gate-bypass surface T1 exists to prevent; a structural Protocol limits the
  gate to read-only market data.
- **Point 7 — estimate cost inside `check_preview` from a fresh quote**:
  rejected — duplicates client-side costing logic (ADR-0002 point 5) in a
  second place, risking the two estimates drifting and one of them silently
  under-costing an order past the cap gates.
- **Point 9 — block Phase 2 close on wiring real realized-P&L tracking**:
  rejected — that tracking is coupled to the Phase 3 pipeline's position
  model, not something Phase 2's gate-mechanism work should block on; the
  breaker is still safety-functional on unrealized P&L alone in the interim.
- **Point 10 — leave `trade_log` successful-place-only and instead narrow SPEC
  §5.1's "one row per attempted order" wording to match**: considered as the
  alternative fix once the contradiction was found — rejected because the
  write path was already fully built and tested for the refusal shape, making
  "wire it in" strictly cheaper than "renegotiate and rewrite the spec," and
  because refusal receipts are independently valuable during the pilot (T4:
  reconstructing why an order was refused, not only why one executed, matters
  for tuning cap thresholds against real gate behavior).

## Consequences

- SPEC §4.3 is amended in this same commit to describe engage/disengage/reset
  as reachable through two equally complete manual channels — local CLI or
  phone via ntfy, authenticated by a TOTP rotating code rather than an
  interactive prompt on the remote side. Future readers of §4.3 should not
  assume "manual" means "requires a terminal session," and should not treat
  the local CLIs as more authoritative than the phone path.
- `NTFY_TOPIC` and `NTFY_COMMAND_SECRET` must both be set, and the secret
  must additionally be set up once in a standard authenticator app (via
  `scripts/generate_totp_secret.py`), before phone control is functional;
  until then, `scripts/remote_listener.py` has nothing to authenticate
  against and the local CLIs' notify step no-ops with a logged warning
  (carried forward from Phase 1's open thread).
- `ConfiguredSafetyGate` construction requires a `PositionsProvider` and a
  `StateStore` in addition to `config` — `server/app.py::create_app` must
  supply both, and any future gate unit test must inject fakes for both
  rather than only a config object.
- The loss-breaker's realized-P&L input is 0 until a Phase 3 change populates
  it from real fills; documented in `docs/PHASE2-REPORT.md` as an open
  thread, not a silent gap.
- `scripts/remote_listener.py` is not itself scheduled (no launchd unit yet)
  — running it is a manual, opt-in step through at least Phase 4/5, consistent
  with SPEC §7's phase boundaries; this phase only builds the mechanism.
- Every safety-state change (local or remote) is now attributable to an
  operator string in `kill_switch.changed_by` / `caps_state.breaker_reset_by`
  — an anonymous safety-state change should never appear in the data model
  after this phase.
- `preview_order` now requires `state`/`config`/`run_id` (point 10) — any
  future caller (e.g. a Phase 3 pipeline invoking it directly rather than
  through the MCP tool wrapper) must supply all three, not just
  `client`/`gate`/`store`/`order`.
- `trade_log` now genuinely matches SPEC §5.1: querying it for a given
  `run_id` reconstructs every attempted order that reached a gate with real
  order data, executed or refused — not only the successful subset.
