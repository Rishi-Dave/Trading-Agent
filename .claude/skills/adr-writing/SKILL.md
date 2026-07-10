---
name: adr-writing
description: Use when making a non-obvious decision — choosing a library, shaping a schema, changing a contract, cap, whitelist tier, or wall threshold — before committing it.
---

# ADR Writing

## When It Warrants an ADR

Test: **would a future session ask "why is it like this?"** If yes, ADR.

Warrants one: architectural shape (pipeline structure choice, §6 spike outcome),
external integrations (news source, notification channel), persistence/schema changes
(§5.1), contract changes (§4.1 refusal shape, §5.2 tool contracts, `OrderRequest`
fields), any cap/whitelist/policy loosening or tightening (§8.2 — same commit as the
config_version bump), wall-threshold changes or flipping a wall job to blocking.

Doesn't: variable naming, test refactors that keep assertions identical, dependency
patch bumps, formatting, docs typos.

## Template

`docs/decisions/NNNN-short-title.md` (next number; zero-padded to 4):

```markdown
# NNNN — Title

**Date:** YYYY-MM-DD
**Status:** accepted

## Context
Why a decision was needed; what constraints applied.

## Decision
What was chosen, concretely.

## Alternatives
What was rejected and the one-line reason each.

## Consequences
What this commits us to; what becomes easier/harder.
```

## Rules

- **Same-commit rule:** the ADR lands in the commit that implements the decision —
  never "I'll document it later."
- One ADR per feature: batch the related judgment calls of one piece of work into a
  single ADR rather than five micro-ADRs.
- Add a one-line entry to `docs/decisions/INDEX.md` in the same commit.
- ADRs are authoritative over auto-memory/episodic memory on conflict. On SPEC §2/§4
  changes, the ADR accompanies the spec edit.
