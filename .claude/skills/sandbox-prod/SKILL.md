---
name: sandbox-prod
description: Use before ANY action that could touch the production E*Trade API or place a real-money order — switching config environments, editing API base URLs, changing ETRADE_SANDBOX, or running the runner outside tests.
---

# Sandbox → Prod Discipline

Sandbox is the default environment everywhere, always (`ETRADE_SANDBOX=1`,
`[environment] mode = "sandbox"`). Production is opt-in per action, never ambient.

## Before Anything Touches Prod — Walk This Out Loud

1. **Which environment is active?** Read `config/config.toml` `[environment] mode` and
   `.env` `ETRADE_SANDBOX` (read the *setting*, don't print the secrets around it).
   If either says sandbox, prod code paths must not be reachable.
2. **Is prod even allowed yet?** Before Phase 6 opens (SPEC §7), the answer is **no** —
   there is no legitimate reason for a dev session to point at prod. Stop here.
3. **Kill-switch state known?** Query it via the CLI. It ships engaged on a fresh DB
   (SPEC §4.3). For any first prod interaction, it must be engaged until the cutover
   checklist completes.
4. **Caps loaded and sane?** The server must be running with explicit caps (T5) and a
   funded, isolated pilot amount (§8.1). "I'll set caps after this one test" is a stop.
5. **Schema-drift re-run?** Prod response shapes must be re-verified against fixtures
   before the first real order (§5.4).
6. **Notification path live?** A prod order without a working ntfy topic means a trade
   nobody hears about. Verify a test ping first.

## Hard Rules

- Never run the real order path from an interactive dev session without step 3 verified.
- Never edit a base URL "temporarily" to prod for debugging — record a sandbox fixture
  instead.
- The Phase 6 cutover gets its own written checklist (drafted at Phase 6 planning);
  this skill is the interim guard, not a substitute for it.
