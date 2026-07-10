# Phase 1 Kickoff Prompt — E*Trade MCP Server Foundation

Paste this to start a Phase 1 session. Model: `opusplan` (pinned in `.claude/settings.json`).

---

You are implementing **Phase 1 of SPEC §7** for the etrade-agent repo: the E*Trade MCP
server foundation — OAuth 1.0a, the six read/preview/place tools against the **sandbox**,
recorded fixtures, and replay tests. Operating doctrine, model policy, and invariants
are in CLAUDE.md; T1–T6 are non-negotiable.

## Step 0 — Open-questions gate (answer before writing code)

1. **Sandbox credentials present?** Check that `.env` has non-empty
   `ETRADE_CONSUMER_KEY` / `ETRADE_CONSUMER_SECRET` (check presence — do not print
   values). If absent, STOP and tell Rishi; the E*Trade developer-key request is a
   user action (SPEC §10).
2. **Token renewal approach** (SPEC §10): E*Trade access tokens idle out after 2 hours
   and hard-expire nightly. Decide with Rishi: interactive renew step per run, or an
   automated renew flow in `etrade/oauth.py`. Write the ADR before implementing.
3. **Preview-id persistence**: preview→place binding (T2) — in-memory per server
   process, or in SQLite? Propose, confirm, ADR (can batch with the renewal ADR).

## Context to load (context diet — nothing more)

- CLAUDE.md (invariants + doctrine)
- SPEC §3 (architecture), §4 (gates that Phase 2 will implement — your tool handlers
  must leave the gate call sites in place), §5.2 (tool contracts), §5.4 (fixtures), §7
  (Phase 1 row), §10 (open questions)
- docs/decisions/0001 (toolchain)
- Skills that will fire: etrade-fixtures, spec-compliance, adr-writing, safety-wall

## Deliverables

1. `etrade/oauth.py` — request-token → browser authorize → access-token dance
   (`scripts/oauth_login.py` as the interactive entry), HMAC-SHA1 signing via
   requests-oauthlib, renewal per the Step-0 ADR, tokens persisted to gitignored
   `tokens/` (T3).
2. `etrade/client.py` — typed client for the six endpoints against sandbox base URLs.
3. `server/tools.py` + `app.py` — the six MCP tools per SPEC §5.2, each order-mutating
   handler calling the (stub) safety gate so Phase 2 slots in without re-plumbing (T1/T2).
4. Fixtures for every endpoint per etrade-fixtures skill; replay + schema-drift tests.
5. **Phase 1 wall** (`tests/wall/`, committed red first): fixture replay + schema-drift
   per endpoint. Flip the CI `phase1-wall` job blocking at phase close, with ADR.
6. Hand-test: interactive Claude Code session using `.mcp.json` to call each tool
   against sandbox — before any autonomous loop exists.

## Standing warnings

- Never print `.env` values, tokens, or account numbers — in code, logs, echo, or debug
  output (T3). The secret-guard hook will block some of it; don't rely on it.
- Sandbox only. Prod base URLs do not appear in this phase at all (sandbox-prod skill).
- Full gates once before push: `uv run ruff check . && uv run mypy && uv run pytest`
  plus the wall run. CI is the arbiter.
- Close the phase with a short `docs/PHASE1-REPORT.md` post-mortem (what shipped, what
  drifted from spec, open threads for Phase 2).
