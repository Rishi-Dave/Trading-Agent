"""Render launchd/*.plist.template into ~/Library/LaunchAgents (SPEC §9).

Usage: uv run python scripts/generate_plist.py [--hour H] [--minute M]
Fills {{LABEL}}, {{WORKDIR}}, {{PATH}}, {{HOUR}}, {{MINUTE}} and prints the
launchctl load/unload commands — it never runs them itself; scheduling is a
deliberate, separate operator action.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path

_LABEL = "com.rishi.trading-agent.decision-run"
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE_PATH = _REPO_ROOT / "launchd" / f"{_LABEL}.plist.template"
_DEFAULT_LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def render_plist(
    template_text: str,
    *,
    label: str,
    workdir: Path,
    path_env: str,
    hour: int,
    minute: int,
) -> str:
    """Fill the five SPEC §9 template placeholders. WorkingDirectory/PATH are
    baked in as absolute values so launchd's minimal, non-login-shell
    environment (SPEC §9: "claude availability + Max OAuth under launchd
    must be verified") can find `uv`/`claude` and the repo regardless of who
    or what triggers the agent."""
    return (
        template_text.replace("{{LABEL}}", label)
        .replace("{{WORKDIR}}", str(workdir))
        .replace("{{PATH}}", path_env)
        .replace("{{HOUR}}", str(hour))
        .replace("{{MINUTE}}", str(minute))
    )


def _run(
    *,
    workdir: Path,
    template_path: Path,
    launch_agents_dir: Path,
    path_env: str,
    hour: int,
    minute: int,
    output: Callable[[str], None] = print,
) -> int:
    if not template_path.exists():
        output(f"template not found: {template_path}")
        return 1
    if not (0 <= hour <= 23):
        output(f"--hour must be 0-23, got {hour}")
        return 1
    if not (0 <= minute <= 59):
        output(f"--minute must be 0-59, got {minute}")
        return 1

    rendered = render_plist(
        template_path.read_text(),
        label=_LABEL,
        workdir=workdir,
        path_env=path_env,
        hour=hour,
        minute=minute,
    )

    launch_agents_dir.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents_dir / f"{_LABEL}.plist"
    plist_path.write_text(rendered)

    output(f"wrote {plist_path}")
    output("")
    output("To schedule the decision run:")
    output(f"  launchctl load {plist_path}")
    output("To unschedule it:")
    output(f"  launchctl unload {plist_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Render the decision-run launchd plist (SPEC §9) and print the "
            "launchctl load/unload commands (never runs them)."
        )
    )
    parser.add_argument(
        "--hour",
        type=int,
        default=9,
        help="Local hour (0-23) to run at daily (default: 9, market-open-ish).",
    )
    parser.add_argument(
        "--minute", type=int, default=35, help="Local minute (0-59) to run at (default: 35)."
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=_REPO_ROOT,
        help="Repo root launchd's WorkingDirectory points at (default: this repo).",
    )
    args = parser.parse_args()

    # launchd's own runtime environment is minimal (SPEC §9) — the
    # *rendering* process's PATH (this shell, wherever uv/claude/node
    # actually live) is what gets baked into the plist verbatim; launchd
    # never inherits a login shell's PATH on its own.
    path_env = os.environ.get("PATH", "")
    if not path_env:
        print("PATH is empty in this environment — refusing to render a plist with no PATH")
        return 1

    return _run(
        workdir=args.workdir,
        template_path=_TEMPLATE_PATH,
        launch_agents_dir=_DEFAULT_LAUNCH_AGENTS_DIR,
        path_env=path_env,
        hour=args.hour,
        minute=args.minute,
    )


if __name__ == "__main__":
    sys.exit(main())
