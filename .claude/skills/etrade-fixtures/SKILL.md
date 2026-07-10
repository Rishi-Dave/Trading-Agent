---
name: etrade-fixtures
description: Use when recording an E*Trade sandbox response fixture, writing or updating a replay test, or a schema-drift test fails after an E*Trade response shape changes.
---

# E*Trade Fixtures

## Overview

Every E*Trade endpoint the client touches gets a recorded real sandbox response
(SPEC §5.4), a replay test (client parses the fixture into its pydantic model), and a
schema-drift test (a fresh live response still matches the recorded shape — fails
loudly, never silently).

## Path Convention

```
fixtures/etrade/<endpoint>.<key-params>.<YYYY-MM-DD>.json
```

Examples: `get_quote.symbol-SPY.2026-07-15.json`, `preview_order.EQ-BUY-limit.2026-07-16.json`.

## Recording Procedure

1. Record **once, from the real sandbox**, via `uv run python scripts/record_fixture.py`
   — never hand-write or synthesize a fixture.
2. Pydantic-validate at record time — a fixture that doesn't parse into its model is
   rejected at recording, not discovered later.
3. **Scrub before saving (T3):** remove/replace all `oauth_*` parameters, account
   numbers/IDs/keys, and anything derived from `.env`. The recorder does this
   automatically; verify by inspection before committing. Fixtures are committed —
   an unscrubbed fixture in git history is a T3 incident.
4. Re-recording an existing fixture gets a new dated filename; keep the old one until
   the replay tests that used it are updated in the same commit.

## When a Schema-Drift Test Fails

The response shape changed upstream. Do NOT edit the fixture to match the new output
(see safety-wall's rationalization table). Instead: re-record from the sandbox, update
the pydantic model deliberately, update replay tests, note the drift in the commit —
and if the change touches an order/money field, walk spec-compliance before committing.

## Sandbox Caveat

E*Trade's sandbox returns canned data (fixed quotes, fake accounts). Fixtures recorded
there prove *shape*, not values — and prod shapes can differ. **Schema-drift tests must
be re-run against production at Phase 6 cutover** before any real order (SPEC §7).
