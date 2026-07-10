# etrade-agent

Capped-autonomy E*Trade trading pilot. Full spec: @docs/SPEC.md — the single authority; cite § numbers, don't restate.

## System Invariants (IMPORTANT)

T1–T6 (quoted from SPEC §2) are load-bearing. A change that violates one is rejected regardless of how useful the feature is.

- **T1 — Safety is enforced in the MCP server, never in the prompt.** Caps, whitelist, circuit breaker, kill switch, and policy gates are code paths in `src/etrade_agent/server/safety.py` that run on every order-mutating tool call. Prompt-side risk checks are advisory belt-and-suspenders; a change that moves enforcement into prompt text is a bug even if the prompt is "very clear."
- **T2 — No order reaches E*Trade without a preview.** `place_order` executes only an order previewed through `preview_order` in the same run; the preview result is what the safety gate evaluates. There is no direct-place code path.
- **T3 — Secrets never appear in code, logs, fixtures, or transcripts.** OAuth keys/tokens enter only via `.env` / gitignored `tokens/`. The logger redacts; fixtures are scrubbed at record time; the PreToolUse hook is defense-in-depth, not the enforcement. A secret printed anywhere is an incident, not a style issue.
- **T4 — Every executed trade carries reasoning receipts.** `trade_log` records reasoning summary, signals consulted, and a caps-state snapshot. A trade whose "why" cannot be reconstructed did not happen correctly.
- **T5 — Caps are explicit or the system refuses to run.** Per-trade %, daily trade count, daily loss % have no defaults anywhere. Missing/invalid caps abort server startup — never a fallback, never warn-and-continue.
- **T6 — Long-only cash equities in v1, as policy gates.** The order model carries `security_type`/`order_action` so riskier instruments are a future config+ADR change — but v1 gates reject them, in the server (per T1).

## Commands

All commands run through uv (project `.venv/` — never system Python, never bare `pytest`):

- `uv run pytest` — unit suite (wall excluded by default)
- `uv run pytest -m wall --override-ini "addopts="` — safety wall suite (SPEC §7)
- `uv run ruff check .` && `uv run ruff format --check .` — lint/format
- `uv run mypy` — strict typecheck
- `uv sync` — (re)install deps into `.venv/`

## Pointers

- @docs/SPEC.md — spec (§2 invariants, §4 safety gates, §7 phases & walls)
- @docs/decisions/INDEX.md — ADR index, the authoritative decision record

## Operating Doctrine — Cost-Tiered Rigor (MANDATORY)

**DIRECT MODE (default).** The main agent reads, edits, tests, and commits directly. Applies to: bug fixes, docs, ADRs, config, single-module changes, merges. Most sessions never leave this mode.

**FAN-OUT MODE (deliberate exception).** Dispatch subagents only when (a) 2+ module boundaries can genuinely proceed in parallel (one worktree + one agent per module, never two per module, briefs per the subagent-briefs skill) or (b) a single task is context-heavy enough that isolating it protects the main context.

**Hard bans:**
- Never resume a subagent whose transcript is large (~100k+ tokens) — dispatch a fresh one with a written brief.
- Never re-run the full gate suite after every step. Module-scoped tests while working; full gates (`ruff` + `mypy` + `pytest` + wall) once before push. CI is the arbiter.

## Model Policy (MANDATORY)

- Session model is pinned via `.claude/settings.json`: `"opusplan"` (plan = Opus, execution = Sonnet).
- Every subagent dispatch names a model explicitly: haiku = mechanical/transcription; sonnet = implementation, reviews, ADRs; opus/fable only with a one-line justification.
- Expensive loops: checkpoint findings to a file and continue in a fresh session; instrumentation output goes to files, only summaries enter context.

## Review Policy

During a phase, the wall + CI arbitrate — no per-diff review agents. One whole-branch review immediately before phase close, one fix pass, done. Walls are committed red before implementation starts and are never weakened to pass (safety-wall skill).

## Context Diet

Briefs and prompts name only the files, SPEC §§, and ADRs the task needs (≤ ~3 ADRs). Subagent reports go to scratchpad files; agents return status + decisions only.

## ADR Rule

ADRs (`docs/decisions/NNNN-title.md` + INDEX.md line) are required for: architectural shape, external integrations, persistence/schema changes, contract changes, cap/whitelist/policy changes (SPEC §8.2), wall-threshold changes. One ADR per feature (batch related decisions). Same-commit rule: the ADR lands in the commit that implements it. ADRs are authoritative over auto-memory/episodic memory on conflict.

## Trading Standing Orders

- **Sandbox is the default environment everywhere.** `ETRADE_SANDBOX=1` and `[environment] mode="sandbox"` unless the sandbox-prod skill checklist has been walked, out loud, in the session.
- Anything that could touch the production E*Trade API — switching config env, editing base URLs, running the runner outside tests — requires the **sandbox-prod** skill first.
- Never exercise the real order path from an interactive dev session without first verifying kill-switch state (it ships engaged on a fresh DB, SPEC §4.3).
- Before committing any change under `src/`, walk the **spec-compliance** skill checklist (T1–T6, by code path).
- Never print `.env` values, tokens, or account numbers — not in echo, not in logs, not "just to debug" (T3).

## Methodology

Superpowers workflows govern planning and TDD. Project skills and when they fire: **safety-wall** (wall is red / pressure to weaken it), **etrade-fixtures** (recording/replaying sandbox responses), **spec-compliance** (pre-commit checklist under `src/`), **adr-writing** (non-obvious decisions), **sandbox-prod** (anything touching prod), **subagent-briefs** (FAN-OUT mode dispatches).
