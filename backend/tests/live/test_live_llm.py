"""Real LLM tests (design section 13.7): opt-in with `uv run pytest -m live`.

Skipped unless the provider is configured (GITHUB_TOKEN for GitHub Models, FOUNDRY_* for
Foundry; `LIVE_PROVIDER=foundry` selects Foundry). About 40 requests: within GitHub Models'
daily limit. Never run in CI.
"""

import os
from functools import cache

import pytest
from helpers import run, source

from pundit.agents.contracts import ClaimRequest
from pundit.cli import ENV_FILE, EVAL_FILES
from pundit.llm.settings import ConfigError, Settings, read_env_file
from pundit.orchestration.evaluation import load_eval, run_eval
from pundit.orchestration.service import ClaimService

pytestmark = pytest.mark.live


@cache
def live_settings() -> Settings | None:
    provider = os.environ.get("LIVE_PROVIDER", "github")
    env = {
        **read_env_file(ENV_FILE),
        **os.environ,
        "USE_MOCK_LLM": "false",
        "LLM_PROVIDER": provider,
    }
    try:
        return Settings.from_env(env)
    except ConfigError:
        return None


@pytest.fixture
def service() -> ClaimService:
    settings = live_settings()
    if settings is None:
        pytest.skip("no live LLM configured (set GITHUB_TOKEN or the FOUNDRY_* variables)")
    return ClaimService(settings)


@pytest.mark.parametrize(
    ("text", "verdict"),
    [
        ("Đội nhà áp đảo hiệp 2", "TRUE"),
        ("Kestrel had more corners than Harbor in the second half", "FALSE"),
        ("Trọng tài thiên vị Harbor", "INSUFFICIENT_DATA"),
    ],
)
def test_smoke(service: ClaimService, text: str, verdict: str) -> None:
    request = ClaimRequest(match_id="dominant_loser-42", text=text, include_trace=True)
    result = run(service.verify(request, source(), claim_id="dominant_loser-42-live"))
    assert result.verdict == verdict
    assert result.guardrail.passed
    assert "orchestrator_fallback" not in result.degraded
    llm_calls = [s for s in result.trace or () if s.kind == "llm"]
    assert llm_calls and all(s.model == service.model for s in llm_calls)


def test_eval_with_the_real_model(service: ClaimService) -> None:
    report = run(run_eval([load_eval(path) for path in EVAL_FILES], service))
    print("\n".join(report.lines()))
    assert not report.wrong, [(r.case.id, r.result.verdict) for r in report.wrong]
    assert report.leaks == 0
    assert report.accuracy >= 0.9
