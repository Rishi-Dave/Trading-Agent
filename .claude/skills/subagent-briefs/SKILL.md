---
name: subagent-briefs
description: Use when dispatching a mutation subagent for phase work, writing its task prompt, or setting up git worktrees for parallel module-boundary agents.
---

# Subagent Briefs

## Overview

Every mutation subagent (one that edits files, not a read-only research agent) gets a
brief that fully bounds its scope, so the orchestrator can verify the result without
re-deriving what the agent was supposed to do.

## When to Dispatch At All

Mirrors CLAUDE.md's Operating Doctrine:

- **Direct mode (default).** The main agent reads, edits, tests, and commits directly
  for serial work — bug fixes, docs, ADRs, config, single-module changes, merges. No
  brief, no worktree, no subagent.
- **Fan-out mode (deliberate exception).** Dispatch only when (a) two or more module
  boundaries (§3.1 — e.g. `etrade/` and `store/`) can genuinely proceed in parallel —
  one worktree and one agent per module, never two agents mutating the same module
  concurrently — or (b) a single task is context-heavy enough (e.g. implementing a
  whole module against a red wall) that isolating it protects the main session.

## Mandatory Brief Template — All Six Parts

1. **Objective** — the one thing this agent exists to build or fix.
2. **Exact file paths in scope** — explicit list or glob; a hard boundary, not a
   suggestion.
3. **Relevant SPEC.md sections** — cite by § number (e.g. "§4.2 gates", "§5.2 tool
   contracts") so the agent isn't relying on memory of the spec.
4. **Acceptance criteria** — the exact checks the orchestrator will run (which wall/
   tests, `uv run ruff check .`, `uv run mypy`). The agent does not grade itself.
5. **Report instruction** — report the *decisions* made, not just "done", so judgment
   calls that need an ADR get caught (adr-writing).
6. **Model** — named explicitly, never inherited: haiku (mechanical), sonnet (default:
   implementation, reviews, ADRs), opus/fable only with a one-line justification.

Missing any part → the brief is incomplete; don't dispatch.

## Worktree Etiquette

- One git worktree per agent per phase; one agent per module boundary (§3.1).
- Merge order follows dependency direction: leaves (`config`, `logs`) → `etrade`/`store`
  → `server`/`pipeline` → `runner`.
- Full gates must pass inside each worktree before it merges.
- **SPEC §4/§5 contracts and `tests/wall/` are read-only to execution agents** — an
  execution agent touching gates' contracts or wall tests is out of scope by
  definition, brief or no brief.
- The phase wall runs only on the merged result (safety-wall) — a green worktree is
  necessary, not sufficient.

## Orchestrator Responsibilities

- Verify acceptance criteria yourself; a subagent's "done, tests pass" is not
  verification.
- On failure: in fan-out mode dispatch a **fix subagent** with the failure details
  (direct patches bypass the discipline that makes parallelism safe); in direct mode
  just fix it.
- Never resume a subagent with a large transcript (~100k+ tokens); dispatch fresh with
  a tight brief.
