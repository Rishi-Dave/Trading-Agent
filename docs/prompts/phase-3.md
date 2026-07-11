# Phase 3 Kickoff Prompt — Decision Pipeline

Paste this to start a Phase 3 session. Model: `opusplan` (pinned in `.claude/settings.json`).

---

You are implementing **Phase 3 of SPEC §7** for the etrade-agent repo: the decision
pipeline — the shape spike, analyst/aggregator/trader steps, a WebSearch news
source, and reasoning receipts flowing to `trade_log` for real. Operating
doctrine, model policy, and invariants are in CLAUDE.md; T1–T6 are
non-negotiable. Phase 2 (full safety layer: every §4.2 gate, the SQLite
store, reset/kill CLIs, TOTP-authenticated remote triggering) is done and
merged (`e85cd8a`) — read `docs/PHASE2-REPORT.md` and
`docs/decisions/0003-phase2-safety-layer.md` before touching anything,
especially the open threads Phase 2 flagged for this phase.

## Step 0 — Open-questions gate (answer before writing code)

1. **Pipeline shape.** SPEC §6 is explicit: "Shape: undecided" — this phase's
   own spike, not a pre-made call. Skim both reference repos before
   committing to anything:
   - **TradingAgents** (TauricResearch) — analyst agents → bull/bear debate
     → trader → risk manager → portfolio manager.
   - **AI Hedge Fund** (virattt) — investor-persona agents → risk manager →
     portfolio manager.

   Evaluate against SPEC §6's stated criteria: simplicity (fewer prompts to
   audit), auditability of reasoning receipts, LLM turns per run
   (subscription-token cost — CLAUDE.md's cost-tiered rigor applies here as
   much as to agent dispatch). SPEC's own bias: "start simple, add debate
   rounds later once the base loop is trusted" — don't build the full debate
   loop on the first pass unless the spike concludes it's cheap enough.
   Propose a shape, confirm with Rishi, ADR it.

2. **How the pipeline actually calls an LLM.** SPEC §9 describes
   `runner/headless.py` (Phase 4, not built yet) as "subprocess `claude -p`
   with `--allowedTools` whitelist." SPEC §6 says the news source "uses
   Claude Code's built-in WebSearch during decision runs." Neither spec text
   nor the module map (§3.1: `pipeline/` owns "protocols, news-source
   interface"; imports only `config`, `logs`, `etrade/models`) pins down
   whether pipeline steps ARE headless Claude Code invocations (each
   analyst/trader step is its own `claude -p` prompt with tool access), or
   whether `pipeline/` is pure Python business logic that calls an LLM API
   directly and only the Phase-4 runner ever shells out to `claude -p`. This
   materially changes what "Phase 3 wall: given fixed inputs, Decision is
   schema-valid" even means (deterministic fixture replay vs. a live-model
   dependency in wall tests). Propose, confirm, ADR (batch with #1).

3. **`Decision.signals` shape.** SPEC §6 freezes the `Decision` dataclass
   (`action`, `symbol`, `quantity`, `confidence`, `reasoning_summary`,
   `signals`) but only describes `signals` as "a list of dated evidence
   items — these become the T4 receipts." The exact schema of one evidence
   item (source, date, symbol, headline/summary, sentiment?) isn't
   specified, and it's what `trade_log.signals_json` will actually hold once
   this phase replaces Phase 2's `"[]"` placeholder. Propose a schema,
   confirm, ADR (batch with #1–#2).

4. **Where the advisory risk check lives.** SPEC §6: "The pipeline's risk
   check is advisory (T1); output flows to the runner, which calls MCP
   tools; the server enforces." T1 is unambiguous that this can never
   *become* enforcement no matter how confident the prompt sounds — but
   granularity is open: a dedicated `risk-advisor` `PipelineStep`, or folded
   into the `trader` step's own reasoning? Propose, confirm, ADR (batch with
   #1–#3).

## Context to load (context diet — nothing more)

- CLAUDE.md (invariants + doctrine), `docs/PHASE2-REPORT.md`,
  `docs/decisions/0003-phase2-safety-layer.md` (recent — `trade_log`
  receipt shape this phase populates for real, `today_utc()`/`StateStore`
  this phase's receipts will write through)
- SPEC §6 in full (decision pipeline — frozen `Decision`/`PipelineStep`
  contracts, the shape-spike framing, the `NewsSource` protocol), §3.1
  (module map — `pipeline/` dependency rules: never imports `server/`,
  nothing imports `runner/`), §5.1 (`trade_log` schema — `signals_json`/
  `reasoning_summary` columns this phase stops leaving as placeholders),
  §7 (Phase 3 row + pipeline wall), §9 (WebSearch/headless framing, for
  context on how Phase 4's runner will eventually drive this — don't build
  Phase 4 itself)
- Current stubs: `pipeline/` doesn't exist yet — this phase creates it from
  the frozen contracts, not from scratch design (SPEC §6 already specifies
  `Decision` and `PipelineStep`)
- Skills that will fire: adr-writing (Step 0's spike decision),
  spec-compliance (T1 — advisory-only risk check; T4 — receipts genuinely
  populated), safety-wall only if this phase's wall touches `tests/wall/`
  (the pipeline wall is new — confirm whether it lives under `tests/wall/`
  per SPEC §7's table or is a plain test suite; SPEC doesn't explicitly say
  `wall`-marked, unlike Phases 1/2's named walls — resolve this as part of
  Step 0 or a quick spec-compliance check, don't assume)

## Deliverables (SPEC §7 Phase 3 row)

1. **Spike ADR** — the Step 0 decisions above, batched.
2. **`pipeline/steps.py`** — the frozen `Decision` dataclass and
   `PipelineStep` protocol (`run(context) -> context`), for real.
3. **Analyst/aggregator/trader steps** — shape per the spike decision.
   Shape-agnostic composition: "steps compose in a list; a role... is just a
   step. No LangGraph or framework dependency" (SPEC §6) — don't introduce
   one.
4. **`pipeline/news.py`** — the `NewsSource` protocol
   (`headlines(symbol, since) -> list[NewsItem]`) plus a WebSearch-backed
   v1 implementation. Keep it Finnhub-swappable (SPEC §6) even though v1
   doesn't need a key.
5. **Reasoning receipts flowing to `trade_log` for real** — Phase 2 shipped
   the *mechanism* (`state.write_trade_log`/`record_executed_trade`,
   `_NO_PIPELINE_REASONING` placeholder in `server/tools.py`) with
   placeholder `reasoning_summary`/`signals_json` because there was no
   pipeline yet. This phase's decision output should replace that
   placeholder — trace exactly where `server/tools.py::place_order` /
   `preview_order` currently hardcode it and wire the real `Decision`
   through. `pipeline/` itself never imports `server/` (module map, T1's
   proposal/dispose split) — the *runner* (Phase 4, not this phase) is what
   will eventually call both the pipeline and the MCP tools; for now, decide
   how this phase's own tests exercise the seam without building Phase 4.

## Pipeline wall (SPEC §7 Phase 3 row)

"Given fixed inputs, Decision is schema-valid, receipts present, advisory
risk check runs." Resolve during Step 0 whether this lives under
`tests/wall/` (`wall`-marked, blocking-eventually per the Phases 1/2
pattern) or is a plain deterministic test suite — SPEC's phrasing doesn't
use the word "wall" as pointedly as Phases 1/2's fixture/cap walls did.
Whichever you land on, commit it before the step implementations that must
satisfy it (safety-wall skill's TDD discipline, if it ends up `wall`-scoped;
regular TDD otherwise).

## Standing warnings

- **T1 still means `server/safety.py` is the only enforcement.** The
  pipeline's risk-advisor step (Step 0 #4) can refuse, warn, or flag — none
  of that is enforcement. A change that lets pipeline output skip or weaken
  a §4.2 gate is a bug regardless of how well-reasoned the prompt is.
- **`pipeline/` never imports `server/`** (module map, §3.1) — the pipeline
  proposes, the server disposes. If a step needs live positions/quotes,
  it's a `NewsSource`-shaped read-only dependency, not a path back into
  `EtradeClient`/MCP tool internals.
- **No framework dependency** (SPEC §6) — steps compose as a plain list; do
  not add LangGraph or an equivalent orchestration library.
- **T4 receipts must be genuinely reconstructible**, not merely non-empty.
  Phase 2's placeholder (`"no decision pipeline yet (Phase 3)..."`,
  `signals_json="[]"`) was acceptable *because* there was no pipeline; this
  phase removing the placeholder without the replacement being real content
  (an actual reasoning trace, actual dated evidence) would be a regression
  dressed as progress.
- **Determinism for the wall.** If pipeline steps are live LLM/WebSearch
  calls (Step 0 #2), "given fixed inputs" for the wall likely means
  fixture-recorded news/quote inputs and a fixture-recorded or mocked model
  response, not a live call in CI — decide this explicitly, don't let it
  default into a flaky live-network test suite (etrade-fixtures skill's
  recording discipline is the closest existing pattern, even though this
  isn't E*Trade data).
- **Sandbox only, still** (sandbox-prod skill) — this phase adds no new
  prod surface; a news/LLM call is not an E*Trade call, but don't let that
  become an excuse to loosen anything order-related.
- **Real cap numbers / pilot capital** — still open (SPEC §10), still not
  blocking. This phase builds the pipeline mechanism; it doesn't need real
  money to prove Decision objects are schema-valid and receipts are
  genuine.
- **`realized_pnl` population** (Phase 2's carried-forward open thread,
  `docs/PHASE2-REPORT.md`) depends on this phase's position-tracking, if any
  — note explicitly whether Phase 3 addresses it or pushes it to Phase 4;
  don't let it silently vanish from the open-thread list a second time.
- Full gates once before push: `uv run ruff check . && uv run ruff format --check .
  && uv run mypy && uv run pytest` plus the wall run
  (`uv run pytest -m wall --override-ini "addopts="`, still must include the
  caps wall and Phase 1 fixture wall green regardless of where the pipeline
  wall lands).
- Close the phase with a short `docs/PHASE3-REPORT.md` post-mortem (same
  shape as Phases 1–2's), noting what's left for Phase 4 (orchestration:
  `runner/headless.py`, launchd, the first real end-to-end run).
