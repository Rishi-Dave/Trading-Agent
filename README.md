# etrade-agent

Capped-autonomy E*Trade trading pilot: a Claude-orchestrated decision pipeline
behind a custom MCP server whose **safety layer (caps, circuit breaker, symbol
whitelist, kill switch) is enforced in code, never in prompts**.

Not investment advice. Systems engineering experiment with a small,
fully-loss-tolerant pilot amount. Sandbox-first.

## Read first

- **[docs/SPEC.md](docs/SPEC.md)** — the single authority (§2 invariants, §4 safety
  gates, §7 phases & walls)
- [docs/decisions/INDEX.md](docs/decisions/INDEX.md) — ADRs
- [CLAUDE.md](CLAUDE.md) — working doctrine for Claude Code sessions

## Setup

```bash
uv venv && uv sync                        # .venv/ + all deps
cp .env.example .env                      # fill in E*Trade sandbox creds + ntfy topic
cp config/config.example.toml config/config.toml
# then EDIT config.toml: caps have no defaults — the server refuses to start
# until you choose them deliberately (SPEC §2 T5)
```

## Gates

```bash
uv run ruff check . && uv run ruff format --check .   # lint
uv run mypy                                           # strict typecheck
uv run pytest                                         # unit suite
uv run pytest -m wall --override-ini "addopts="       # safety wall (blocking in CI)
```

## Status

Bootstrap complete; Phase 1 (E*Trade MCP server foundation) not started.
Kickoff prompt: [docs/prompts/phase-1.md](docs/prompts/phase-1.md).
Blocker: E*Trade developer sandbox key (SPEC §10).
