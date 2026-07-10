---
name: spec-compliance
description: Use before committing any product code change under src/, as a pre-merge checklist walking invariants T1–T6 by actual code path.
---

# Spec Compliance — Pre-Commit Checklist

Walk each invariant against the **actual code path** of the diff, not the intention.
Two minutes of tracing beats an incident.

## The Checklist

1. **T1 — enforcement location.** Does any risk/cap/whitelist/kill-switch decision in
   this diff live in a prompt, a pipeline step, or the runner? If yes: it may exist
   there as *advice*, but the enforcing copy must be in `server/safety.py` and run on
   the tool call. Trace: can any input reach an E*Trade write without passing the gate?

2. **T2 — preview path.** If the diff touches order flow: does `place_order` still
   require a `preview_id` issued this run for an identical order? Did any new code path
   appear that reaches the E*Trade order endpoint without a preview? (grep for the
   client's place call — it should have exactly one caller.)

3. **T3 — secrets.** Any new secret touch? It must come from `.env`/`tokens/` only.
   New log lines: do they pass through `logs.py` redaction? New fixtures: scrubbed?
   Any string literal that looks like a key, token, topic, or account number?

4. **T4 — receipts.** If the diff can execute or refuse a trade: does the `trade_log`
   row still get `reasoning_summary`, `signals_json`, `caps_snapshot_json`? A new
   decision input that isn't captured in receipts breaks auditability.

5. **T5 — no cap defaults.** Did any default value for caps/pilot-capital sneak in —
   in pydantic models, function signatures, test helpers, or `or`-fallbacks? Test
   configs must inject values explicitly.

6. **T6 — policy gates intact.** If the diff touches `OrderRequest` or gate logic:
   are non-EQ security types and short sells still refused by the server? Extending
   the *model* is fine; extending the *policy* requires config_version bump + ADR
   (SPEC §8.2).

## Also

- Cap/whitelist/policy/schema/contract changes in this diff → ADR in the same commit
  (adr-writing skill).
- Wall tests touched? Stop — safety-wall skill governs that.
