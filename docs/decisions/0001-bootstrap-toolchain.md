# 0001 — Bootstrap toolchain and scaffold decisions

**Date:** 2026-07-10
**Status:** accepted

## Context

Bootstrapping the repo from the reworked SPEC. Decisions locked in the planning
session with Rishi (stack, isolation, enforcement) plus the judgment calls made
while scaffolding. Inspiration: Fantasy-Agent's Claude Code discipline
(invariants + skills + blocking CI walls) and Agent-Creation's patterns
(secret-guard hook, `claude -p` adapter) — copied, never imported.

## Decision

- **All Python 3.12, single installable package** (`etrade_agent`, src layout) —
  solo project, one deploy target; module boundaries enforced by convention +
  nested CLAUDE.md (SPEC §3.1), not packaging.
- **uv** with a project `.venv/`: `uv venv` + `uv sync`; every command runs
  through `uv run` — never system Python.
- **Fully standalone** (SPEC §3.2): no dependency on Agent-Creation's
  `agent_factory`; headless adapter and JSONL logging reimplemented in-repo.
- **`mcp` SDK pinned `>=1.9,<2`** (FastMCP has moved between majors), stdio
  transport, registered in `.mcp.json` for interactive Phase 1 hand-testing.
- **`requests` + `requests-oauthlib`** for OAuth 1.0a/HMAC-SHA1 — the
  battle-tested implementation; E*Trade's own Python examples use it; sync is
  fine at this scale.
- **TOML + pydantic config**: stdlib `tomllib` read; pydantic models give
  required-no-default enforcement (T5) and range validation for free. Caps ship
  commented-out in `config.example.toml` so copying it still refuses to run.
- **Wall mechanism: pytest marker + directory** (`tests/wall/`, marker
  auto-applied by conftest; default runs exclude via addopts). One native
  mechanism instead of Fantasy-Agent's env-gated globs (a Vitest workaround).
  The caps wall CI job is blocking from day one.
- **launchd** for scheduling (macOS host, matches existing agent convention);
  plist template sets PATH explicitly because launchd's env is minimal.
- **ntfy.sh** for notifications: zero signup, one stdlib POST; topic is a long
  random string treated as a secret.
- **Secret-guard hook runs on `/usr/bin/python3`** (system): pyenv shims
  intercept bare `python3` and fail on this project's uv-format
  `.python-version`, which would have silently disabled the hook.

## Alternatives

- TS MCP server + Python agent (Agent-Creation split) — two toolchains, safety
  tests split across languages.
- Depending on `agent_factory` — couples the money system to a shared lib that
  evolves with other agents.
- Finnhub/Alpha Vantage for news now — extra key + rate limits; deferred behind
  the `NewsSource` protocol (SPEC §6).
- Env-gated wall selection (FF_WALL=1 style) — pytest markers do it natively.

## Consequences

- Phase 1 sessions start against frozen contracts (SPEC §4/§5) with green gates.
- Riskier instruments later = config_version bump + ADR (SPEC §8.2), no
  restructure (T6).
- The `mcp` pin must be revisited when the SDK hits 2.x.
- Anything invoking bare `python3` in this repo will hit the pyenv/.python-version
  mismatch — always `uv run` (or `/usr/bin/python3` for dependency-free hooks).
- **macOS hidden-flag landmine:** uv's venv scaffolding sets the `UF_HIDDEN` flag
  on some venv files, and CPython ≥3.12.11 skips hidden `.pth` files — which
  silently breaks the editable install (`ModuleNotFoundError: etrade_agent`).
  Pytest is immune (`pythonpath = ["src"]`), but if `python -m etrade_agent...`
  ever fails right after recreating `.venv`, run:
  `chflags nohidden .venv .venv/lib/python3.12/site-packages{,/*.pth}` and re-sync.
