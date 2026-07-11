"""`python -m etrade_agent.runner` entrypoint (SPEC §9) — the launchd plist's
ProgramArguments target (`launchd/com.rishi.trading-agent.decision-run.plist.template`).

Wires the live pipeline seams (ClaudeLLMClient, WebSearchNewsSource) and the
shared Runtime (`server.app.build_runtime`, ADR-0005) into one call to
`runner.decision_run.run_decision`. Every failure this process can hit —
missing/invalid caps, missing OAuth tokens, an unexpected mid-run exception —
is caught here and turned into an ntfy alert + nonzero exit, never a raw
traceback: launchd's `StandardErrorPath` (SPEC §9) is a log file nobody
watches in real time, so letting an exception propagate there is silent
failure, not loud failure (ADR-0002 point 9's carried-forward concern).

`llm`/`news`/`notify` are injectable so this module's own responsibility —
wiring + failure classification — is testable without a live `claude` process
or live E*Trade network calls; production use (the `if __name__ ==
"__main__"` block) always passes real, live-backed seams.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from etrade_agent import logs
from etrade_agent.config import ConfigError
from etrade_agent.pipeline.llm import LLMClient
from etrade_agent.pipeline.news import NewsSource, WebSearchNewsSource
from etrade_agent.runner.decision_run import NotifyFn, build_notify, run_decision
from etrade_agent.runner.headless import is_claude_available
from etrade_agent.runner.llm_client import ClaudeLLMClient
from etrade_agent.server.app import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_TOKENS_DIR,
    ServerStartupError,
    build_runtime,
)

_AGENT_ID = "etrade-runner"
DEFAULT_LOG_DIR = Path("logs")


def main(
    config_path: Path = DEFAULT_CONFIG_PATH,
    tokens_dir: Path = DEFAULT_TOKENS_DIR,
    *,
    llm: LLMClient | None = None,
    news: NewsSource | None = None,
    notify: NotifyFn | None = None,
    log_dir: Path = DEFAULT_LOG_DIR,
) -> int:
    load_dotenv()
    if notify is None:
        notify = build_notify(os.environ.get("NTFY_TOPIC"))

    # Only matters when we're about to construct a REAL ClaudeLLMClient — a
    # test-injected llm makes this check irrelevant, since nothing will shell
    # out to `claude` in that case.
    if llm is None and not is_claude_available():
        message = "claude CLI not found on PATH — check the launchd plist's PATH env (SPEC §9)"
        logs.log(_AGENT_ID, "error", message)
        notify("Decision run FAILED (claude unavailable)", message)
        return 1

    try:
        rt = build_runtime(config_path, tokens_dir)
    except ConfigError as exc:
        message = f"refusing to start (caps-required, SPEC §4.2): {exc}"
        logs.log(_AGENT_ID, "error", message)
        notify("Decision run FAILED (config)", message)
        return 1
    except ServerStartupError as exc:
        message = str(exc)
        # oauth_login.py is the one human step this phase's automated OAuth
        # renewal (ADR-0005, build_runtime's best-effort renew_tokens) cannot
        # replace — surface it distinctly so the alert is actionable.
        title = (
            "Decision run FAILED — run oauth_login.py"
            if "oauth_login" in message
            else "Decision run FAILED (startup)"
        )
        logs.log(_AGENT_ID, "error", message)
        notify(title, message)
        return 1

    resolved_llm: LLMClient = llm if llm is not None else ClaudeLLMClient()
    resolved_news: NewsSource = news if news is not None else WebSearchNewsSource(llm=resolved_llm)

    try:
        run_decision(rt, llm=resolved_llm, news=resolved_news, notify=notify, log_dir=log_dir)
    except Exception as exc:
        # The pipeline's own steps already degrade per-symbol on failure
        # (Phase 3 review) and the safety gate fails closed on its own
        # exceptions — reaching here means something outside either of those
        # (e.g. a live EtradeClient.preview_order call itself failing) went
        # wrong. Must still surface as a clean alert, not a raw traceback.
        message = f"decision run failed unexpectedly: {exc}"
        logs.log(_AGENT_ID, "error", message, error_type=type(exc).__name__)
        notify("Decision run FAILED (unexpected)", message)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
