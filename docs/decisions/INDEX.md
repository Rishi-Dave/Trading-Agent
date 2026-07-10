# ADR Index

One line per ADR. ADRs are the authoritative decision record (over auto-memory /
episodic memory). Same-commit rule: an ADR lands with the change it justifies.

- [0001](0001-bootstrap-toolchain.md) — Bootstrap toolchain: uv + single Python package, standalone (no agent_factory), mcp SDK pinned <2, requests-oauthlib, TOML+pydantic caps-no-defaults, pytest-marker wall (blocking day one), launchd, ntfy.sh, system-python secret-guard hook.
- [0002](0002-phase1-oauth-and-server-wiring.md) — Phase 1 OAuth/server wiring: interactive daily OAuth dance (no unattended renewal), in-memory per-run preview→place binding (T2), shared OAuth host is not a prod-path violation, Phase-1 PassthroughGate (labeled, sandbox-only), client-side estimated_cost computation (E*Trade preview has no total-cost field), accountMode=="IRA" is the real retirement signal, refusals returned not raised (FastMCP ToolError wraps/corrupts the payload), fixture scrubbing by value not just by key.
