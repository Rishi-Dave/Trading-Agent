# 0004 — Phase 3 decision pipeline: shape spike, LLM/news seam, advisory risk placement, signal schema

**Date:** 2026-07-11
**Status:** accepted

## Context

Phase 3 (SPEC §7) builds the decision pipeline against the frozen `Decision`/
`PipelineStep`/`NewsSource` contracts (SPEC §6, `pipeline/steps.py`, `pipeline/news.py`)
that Phase 2's scaffold shipped but deliberately left undecided in shape ("Shape:
undecided" — SPEC §6). The kickoff prompt required four judgment calls be proposed,
confirmed with Rishi, and ADR'd before writing step logic — batched here per the
adr-writing skill, alongside two internal decisions (wall placement, `realized_pnl`
deferral) that followed directly from the four.

Two reference repos were skimmed against SPEC §6's evaluation criteria (simplicity,
receipt auditability, LLM turns/run):

- **TradingAgents** (TauricResearch) — 4 analysts (fundamental/sentiment/news/technical)
  → bull/bear debate → trader → risk management team → portfolio manager. LangGraph.
- **AI Hedge Fund** (virattt) — 14+ investor-persona agents → risk manager → portfolio
  manager. LangGraph, FastAPI backend.

Both are debate-loop, many-agent systems — exactly the complexity SPEC §6's own bias
("start simple, add debate rounds later once the base loop is trusted") says to defer
on a first pass, and neither commits to a specific LLM-invocation mechanism compatible
with this repo's module map (`pipeline/` may import only `config`/`logs`/`etrade.models`;
nothing imports `runner/`, per SPEC §3.1) or its Max-subscription, no-API-key headless
`claude -p` constraint (SPEC §9).

## Decision

**1. Shape: multi-analyst, no debate.** Three analyst steps (news, technical,
fundamental) → a deterministic aggregator → a trader step → advisory risk steps. No
bull/bear debate loop, no investor personas — both are a future config+ADR change if
the base loop proves itself, not a Phase 3 commitment. Chosen over the minimal
single-analyst shape for broader signal coverage per symbol; chosen over a debate-lite
shape because SPEC §6 explicitly says to earn debate rounds after the base loop is
trusted, not build them speculatively. Confirmed with Rishi.

**2. LLM/news access: an injected seam, with the concrete adapter built now.**
`pipeline/` cannot import `runner/` (module map, SPEC §3.1) and Claude Code's WebSearch
tool only exists inside a `claude -p`/`claude --print` invocation (SPEC §9) — so no
pipeline step can shell out directly. The fix is the same pattern already used for
`NewsSource`: an `LLMClient` Protocol (`pipeline/llm.py`) that analyst/trader/risk steps
depend on structurally, with the concrete `claude -p`-backed implementation
(`runner/llm_client.py::ClaudeLLMClient`, wrapping the existing
`runner/headless.py::claude_query`/`run_agent`) built in this phase but *wired into a
live pipeline instance* only by the Phase-4 runner. Tests inject a fake `LLMClient`
returning fixture text — no live network in the pipeline wall. This resolves SPEC §6's
"pipeline is pure Python business logic that calls an LLM API... [vs] each step is its
own `claude -p` prompt" ambiguity: it is pure Python that calls an *injected* LLM seam;
whether that seam happens to shell out to `claude -p` is the concrete adapter's problem,
invisible to step logic and to the wall.

**3. Advisory risk: both a deterministic mirror step and a qualitative LLM step.**
`CapsMirrorRiskStep` mechanically mirrors caps/whitelist/policy against `config`
(zero LLM turns, cannot hallucinate, deterministic for the wall) and `RiskAdvisorStep`
adds a qualitative LLM read on the trader's `Decision` (closer to the reference repos'
risk-manager role, catches judgment risk a mechanical mirror can't). **T1 governs
both equally: neither returns a refusal, neither can block `context.decisions`; each
only appends advisory `Signal`s / `context.notes` entries.** Neither imports
`server/safety` — this is deliberate duplication (CLAUDE.md: "any risk check written
here is advisory and duplicated — the enforcing copy lives in `server/safety.py`"), not
a second enforcement path. `server/safety.py` remains the sole enforcement point
regardless of how well either advisory step reasons.

**4. `Signal.detail` carries source-specific structure; the frozen dataclass is
unchanged.** One evidence item serializes as
`{source, as_of (ISO 8601), summary, detail: {...}}`. News-derived signals put
`url`/`symbol`/`sentiment` inside `detail`; technical/fundamental signals put their
own domain fields there. This keeps `Signal`/`Decision` (frozen SPEC §6 contracts)
untouched — no ADR-worthy contract amendment needed — at the cost of `detail`'s
per-source shape being convention rather than a typed field. `pipeline/steps.py`
gains one `signals_to_json(signals) -> str` helper as the canonical serialization,
so `trade_log.signals_json` has exactly one code path producing it.

**5. Pipeline wall lives at `tests/wall/phase3/`, a new `phase3` marker,
informational-then-blocking.** Mirrors the `tests/wall/phase1/` precedent exactly
(`conftest.py` auto-applies `phase3` on top of `wall`; its own CI job,
`continue-on-error: true` while the phase is open, flipped to blocking at phase close
via ADR). SPEC §7 lists Phase 3's wall by name ("pipeline wall") the same way it names
Phases 1–2's, so it gets the same `tests/wall/` treatment rather than living as a plain
test suite outside the wall mechanism.

**6. `realized_pnl` stays deferred past Phase 3 too.** The pipeline *proposes*
`Decision`s; it does not execute fills or track cost basis, so it has nothing to feed
automatic `realized_pnl` population with. That remains coupled to execution (Phase 4's
runner loop actually placing orders), not to this phase's proposal logic. The
loss-breaker continues to function on live unrealized P&L alone, unchanged from
ADR-0003 points 3 and 9 — not a new gap, the same one carried forward one more phase
and stated explicitly rather than silently dropped from the open-thread list.

## Alternatives

- **Point 1 — minimal single-analyst (news only) → aggregator → trader**: the
  lowest-turn, fewest-prompts option and SPEC §6's most literal reading of "start
  simple" — rejected in favor of broader per-symbol signal coverage now that the
  contracts and LLM seam are being built anyway; the cost is analyst-count, not
  architecture, so it doesn't compound the "start simple" risk the way a debate loop
  would.
- **Point 1 — debate-lite (analyst → bull/bear → trader)**: closer to TradingAgents,
  rejected because it introduces the debate loop SPEC §6 explicitly says to defer,
  before the base loop has run once.
- **Point 2 — protocols + fakes only, defer the concrete `claude -p` adapter to
  Phase 4**: leaner Phase 3, but leaves deliverable #4 ("WebSearch-backed v1
  implementation") unmet until Phase 4 — rejected; the adapter is small and
  self-contained (wraps existing `runner/headless.py` functions), so building it now
  costs little and lets the pipeline wall exercise a real (fixture-faked) shape of the
  seam rather than an untested protocol.
- **Point 3 — risk check folded into the trader step's own reasoning**: fewer
  components, but entangles risk reasoning with the decision itself and produces no
  separately auditable receipt — rejected, since T4 wants the risk read to be a
  distinct, inspectable trace.
- **Point 3 — a single dedicated LLM risk-advisor only**: closest to the reference
  repos' risk-manager role, but non-deterministic end to end with no mechanical
  fallback signal — rejected in favor of pairing it with a deterministic mirror so the
  wall has at least one fully deterministic, hallucination-proof advisory path.
- **Point 4 — add a first-class `sentiment` field to `Signal`**: would surface
  sentiment without digging into `detail`, but amends a contract SPEC §6 explicitly
  froze — rejected; `detail` already exists for exactly this kind of source-specific
  data, and adding a field only for one analyst's convenience isn't worth reopening a
  frozen dataclass.
- **Point 6 — block Phase 3 close on wiring `realized_pnl`**: rejected for the same
  reason ADR-0003 point 9 rejected it for Phase 2 — the dependency is on execution
  (fills/cost-basis), which Phase 3 doesn't do either; forcing it in here would mean
  building Phase 4-shaped code inside Phase 3's scope.

## Consequences

- `pipeline/` gains two new protocols (`llm.py::LLMClient`, `market.py::MarketDataSource`)
  alongside the existing `news.py::NewsSource` — all three are the only way pipeline
  steps reach the outside world; none imports `server/` or `runner/`.
- `runner/llm_client.py` is new surface owned by `runner/` (per the module map, nothing
  else imports it) — it is dead code until Phase 4 constructs a live pipeline with it,
  which is expected and stated, not a loose end.
- `StoredPreview` (`server/preview_store.py`) gains `reasoning_summary`/`signals_json`
  fields bound to the previewed order; `preview_order`/`place_order`
  (`server/tools.py`) stop hardcoding `_NO_PIPELINE_REASONING`/`signals_json="[]"` as
  the only path — they become the default for calls that don't supply pipeline
  reasoning (a direct/manual MCP call), not a placeholder for "no pipeline exists yet."
  Any future caller of `preview_order` must decide whether it has real reasoning to
  supply or is accepting the honest "no pipeline reasoning supplied" default.
- The pipeline wall (`tests/wall/phase3/`) joins the caps wall and Phase 1 fixture wall
  as a third named wall; CI gains a `phase3-wall` job, informational until this phase
  closes with its own flip-to-blocking ADR.
- `realized_pnl` is still 0 at every `caps_state` read through the end of Phase 3 —
  tracked again in `docs/PHASE3-REPORT.md` as a Phase 4 dependency, consistent with how
  ADR-0003/`PHASE2-REPORT.md` tracked it for Phase 2.
- No new framework dependency (no LangGraph) — steps remain a plain list `PipelineStep`
  composition, per SPEC §6 and CLAUDE.md's hard ban repeated in `pipeline/CLAUDE.md`.
