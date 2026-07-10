"""MCP server entrypoint (`uv run python -m etrade_agent.server.app`, see .mcp.json).

Startup enforces gate `caps-required` (SPEC §4.2, T5): the server exits nonzero
before registering any tool if config is missing or caps are invalid. That path is
REAL now and guarded by the caps wall test; tool registration is Phase 1.
"""

from __future__ import annotations

import sys
from pathlib import Path

from etrade_agent.config import AppConfig, ConfigError, load_config

DEFAULT_CONFIG_PATH = Path("config/config.toml")


def create_app(config_path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Validate config or die (T5). Returns the validated config.

    Phase 1 turns this into a FastMCP app factory; the caps-required behavior —
    raise ConfigError before anything else exists — must not change (wall test).
    """
    config = load_config(config_path)
    del config  # Phase 1 hands this to the FastMCP app factory
    raise NotImplementedError("Phase 1 (SPEC §7): construct FastMCP app, register tools")


def main() -> int:
    try:
        create_app()
    except ConfigError as exc:
        print(f"refusing to start (caps-required, SPEC §4.2): {exc}", file=sys.stderr)
        return 1
    except NotImplementedError as exc:
        print(f"server not implemented yet: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
