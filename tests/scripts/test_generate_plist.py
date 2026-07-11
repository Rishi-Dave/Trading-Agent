"""Plist generator (SPEC §9, ADR-0001 scaffold): render the launchd template,
write it to ~/Library/LaunchAgents, print (never run) the launchctl commands."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "generate_plist.py"
_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "launchd"
    / "com.rishi.trading-agent.decision-run.plist.template"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_plist_script", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def generate_plist() -> ModuleType:
    return _load_module()


def test_render_plist_fills_every_placeholder(generate_plist: ModuleType) -> None:
    template_text = _TEMPLATE_PATH.read_text()

    rendered = generate_plist.render_plist(
        template_text,
        label="com.rishi.trading-agent.decision-run",
        workdir=Path("/Users/rishi/repo"),
        path_env="/usr/local/bin:/usr/bin",
        hour=9,
        minute=35,
    )

    assert "{{" not in rendered
    assert "<string>com.rishi.trading-agent.decision-run</string>" in rendered
    assert "<string>/Users/rishi/repo</string>" in rendered
    assert "<string>/usr/local/bin:/usr/bin</string>" in rendered
    assert "<integer>9</integer>" in rendered
    assert "<integer>35</integer>" in rendered
    assert "/Users/rishi/repo/logs/launchd-stdout.log" in rendered
    assert "/Users/rishi/repo/logs/launchd-stderr.log" in rendered
    # ProgramArguments is unchanged by rendering (no placeholder there).
    assert "etrade_agent.runner" in rendered


def test_run_writes_plist_to_launch_agents_dir(tmp_path: Path, generate_plist: ModuleType) -> None:
    launch_agents_dir = tmp_path / "LaunchAgents"
    workdir = tmp_path / "repo"

    code = generate_plist._run(
        workdir=workdir,
        template_path=_TEMPLATE_PATH,
        launch_agents_dir=launch_agents_dir,
        path_env="/usr/local/bin",
        hour=9,
        minute=35,
        output=lambda _: None,
    )

    assert code == 0
    plist_path = launch_agents_dir / "com.rishi.trading-agent.decision-run.plist"
    assert plist_path.exists()
    content = plist_path.read_text()
    assert str(workdir) in content
    assert "{{" not in content


def test_run_prints_launchctl_load_and_unload_commands(
    tmp_path: Path, generate_plist: ModuleType
) -> None:
    launch_agents_dir = tmp_path / "LaunchAgents"
    printed: list[str] = []

    generate_plist._run(
        workdir=tmp_path / "repo",
        template_path=_TEMPLATE_PATH,
        launch_agents_dir=launch_agents_dir,
        path_env="/usr/local/bin",
        hour=9,
        minute=35,
        output=printed.append,
    )

    plist_path = launch_agents_dir / "com.rishi.trading-agent.decision-run.plist"
    joined = "\n".join(printed)
    assert f"launchctl load {plist_path}" in joined
    assert f"launchctl unload {plist_path}" in joined


def test_run_refuses_on_missing_template(tmp_path: Path, generate_plist: ModuleType) -> None:
    code = generate_plist._run(
        workdir=tmp_path / "repo",
        template_path=tmp_path / "does-not-exist.plist.template",
        launch_agents_dir=tmp_path / "LaunchAgents",
        path_env="/usr/local/bin",
        hour=9,
        minute=35,
        output=lambda _: None,
    )

    assert code == 1
    assert not (tmp_path / "LaunchAgents").exists()


@pytest.mark.parametrize("bad_hour", [-1, 24, 100])
def test_run_refuses_invalid_hour(
    tmp_path: Path, generate_plist: ModuleType, bad_hour: int
) -> None:
    code = generate_plist._run(
        workdir=tmp_path / "repo",
        template_path=_TEMPLATE_PATH,
        launch_agents_dir=tmp_path / "LaunchAgents",
        path_env="/usr/local/bin",
        hour=bad_hour,
        minute=0,
        output=lambda _: None,
    )
    assert code == 1


@pytest.mark.parametrize("bad_minute", [-1, 60, 100])
def test_run_refuses_invalid_minute(
    tmp_path: Path, generate_plist: ModuleType, bad_minute: int
) -> None:
    code = generate_plist._run(
        workdir=tmp_path / "repo",
        template_path=_TEMPLATE_PATH,
        launch_agents_dir=tmp_path / "LaunchAgents",
        path_env="/usr/local/bin",
        hour=9,
        minute=bad_minute,
        output=lambda _: None,
    )
    assert code == 1
