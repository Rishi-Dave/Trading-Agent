"""Interactive OAuth driver behavior (SPEC §7 Phase 1, T3: never print secrets).

scripts/ isn't a package on pythonpath (ADR-0001 keeps pythonpath = ["src"]);
load the module by file path instead of adding scripts/ to pythonpath repo-wide.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from etrade_agent.etrade import oauth

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "oauth_login.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("oauth_login_script", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def oauth_login() -> ModuleType:
    return _load_module()


def test_missing_credentials_aborts_without_dance(oauth_login: ModuleType) -> None:
    outputs: list[str] = []

    code = oauth_login._run(None, None, Path("unused"), output=outputs.append)

    assert code == 1
    assert any("ETRADE_CONSUMER" in line for line in outputs)


def test_successful_dance_saves_tokens_and_never_prints_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, oauth_login: ModuleType
) -> None:
    monkeypatch.setattr(
        oauth,
        "begin_authorization",
        lambda key, secret, sandbox: "https://us.etrade.com/e/t/etws/authorize?key=k&token=t",
    )
    monkeypatch.setattr(
        oauth, "complete_authorization", lambda verifier: oauth.OAuthTokens("acctok", "accsecret")
    )
    outputs: list[str] = []

    code = oauth_login._run(
        "ckey", "csecret", tmp_path, prompt=lambda _: "verifier-123", output=outputs.append
    )

    assert code == 0
    loaded = oauth.load_tokens(tmp_path)
    assert loaded is not None
    assert loaded.token == "acctok"
    joined = "\n".join(outputs)
    assert "acctok" not in joined
    assert "accsecret" not in joined
    assert "csecret" not in joined


def test_empty_verifier_aborts(monkeypatch: pytest.MonkeyPatch, oauth_login: ModuleType) -> None:
    monkeypatch.setattr(
        oauth, "begin_authorization", lambda key, secret, sandbox: "https://example/authorize"
    )
    outputs: list[str] = []

    code = oauth_login._run(
        "ckey", "csecret", Path("unused"), prompt=lambda _: "   ", output=outputs.append
    )

    assert code == 1
