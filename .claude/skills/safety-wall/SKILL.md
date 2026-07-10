---
name: safety-wall
description: Use when running the safety wall suite, deciding whether a phase can close, or a wall/cap-violation test is red and there's any pressure (deadline, "almost passing", sunk cost) to adjust the test instead of the code.
---

# Safety Wall

## Overview

The wall (SPEC §7) is the pytest suite in `tests/wall/` (marker `wall`), excluded from
default runs and enforced as its own blocking CI job. Each phase's wall is committed
red *before* implementation starts — the wall is the spec's acceptance bar, written
before anyone could game it. Every §4.2 safety gate maps 1:1 to a named wall test.

**THE RULE: walls are never weakened to pass.** Assertions, thresholds, refusal shapes,
and fixtures are not edited to turn red green. A wall failure means the code is not
done — it does not mean the wall is wrong. This system places real-money orders; a
weakened wall is a live financial hazard, not a test-hygiene issue.

## How to Run

```
uv run pytest -m wall --override-ini "addopts="
```

Run against the merged branch, not a single worktree in isolation.

## Interpreting a Red Wall

The safe direction is always **refuse the order / do less**, never a shortcut that fakes
a pass:
- A gate test failing → the gate refuses incorrectly or allows incorrectly. If in doubt,
  the gate must refuse (fail-closed).
- Refusal-shape test failing → fix the payload to match SPEC §4.1; never loosen the
  assertion to accept a vaguer shape.
- Startup-caps test failing → the server is finding a default somewhere. Hunt the
  default down (T5); do not teach the test about it.

A phase closes **only** when its wall passes in CI on the merged result, at which point
the CI job flips from informational to blocking (with an ADR). SPEC §4/§5 contracts stay
frozen while a phase is open — a wall failure is never resolved by changing a contract
mid-phase.

## Red Flags — Stop

- Editing a fixture so an assertion passes.
- Marking a wall test `skip`/`xfail`, or commenting out an assertion.
- Loosening the §4.1 refusal-shape assertion ("it still refuses, close enough").
- Adding a cap default "just for tests" (violates T5 — inject test config explicitly).
- Changing a §4.2 gate rule mid-phase to dodge a failing test.
- "Just this once" / "it's basically passing" reasoning.

## Rationalization Table

| Excuse | Reality |
|---|---|
| "The cap is too strict for this test scenario" | The cap is the acceptance bar. The scenario is exactly what the wall exists to refuse. Fix the code path. |
| "This fixture is stale, I'll update it to match the new output" | Fixtures are ground truth. Editing them to match broken output erases the signal — re-record from the real sandbox instead (etrade-fixtures). |
| "I'll add a sensible default so the test can construct a config" | That default will ship. T5 exists because a silently-defaulted cap trades real money. Inject explicit test values. |
| "The gate refuses, just with a different error shape" | Downstream code and audits parse §4.1. A drifted shape is a broken contract, not a cosmetic diff. |
| "We're close, one edge case left" | Wall failure is binary. Close isn't done — and the edge case is where the money leaks. |
| "CI's flaky, skip it for now" | Skipping is weakening. Fix the flakiness or the code — never disable the check. |
