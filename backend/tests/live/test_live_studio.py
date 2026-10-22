"""Studio with a real LLM (docs/day3-design.md section 16.8): opt-in with `pytest -m live`.

The first five triggers of importance 2 or more of dominant_loser-42 go through the studio with
the configured model (Ollama by default): the provider must answer and no rejected text may be
published. The whole match only runs with LIVE_STUDIO=1 (or `pundit studio --provider ollama`).
"""

import os

import pytest
from helpers import generated, run
from studio_helpers import triggers, view_at
from test_live_llm import live_settings

from pundit.agents.contracts import StudioJob
from pundit.orchestration.recap_service import RecapService
from pundit.orchestration.studio_eval import build_report, run_offline
from pundit.orchestration.studio_service import StudioService
from pundit.orchestration.studio_steps import StudioBudgets

pytestmark = pytest.mark.live
DL = ("dominant_loser", 42)


def settings_or_skip():  # type: ignore[no-untyped-def]
    settings = live_settings()
    if settings is None:
        pytest.skip("no live LLM configured or reachable (Ollama, LLM_* or FOUNDRY_* settings)")
    return settings


def test_first_important_triggers_with_a_real_model() -> None:
    settings = settings_or_skip()
    service = StudioService(settings, budgets=StudioBudgets.offline(settings))
    important = [t for t in triggers(*DL) if t.importance >= 2][:5]
    calls = failures = 0
    for trigger in important:
        output = run(service.run(StudioJob(trigger=trigger), view_at(*DL, trigger.seq)))
        for span in output.trace or ():
            if span.kind == "llm":
                calls += 1
                failures += int(span.status == "error")
    assert calls and failures <= calls // 2, (calls, failures)


@pytest.mark.skipif(os.environ.get("LIVE_STUDIO") != "1", reason="set LIVE_STUDIO=1")
def test_whole_match_with_a_real_model() -> None:
    settings = settings_or_skip()
    result = run(
        run_offline(
            generated(*DL),
            StudioService(settings, offline=True, llm_min_importance=2),
            RecapService(settings),
        )
    )
    report = build_report(result)
    assert not report.leaks and not report.oracle, "\n".join(report.lines())
