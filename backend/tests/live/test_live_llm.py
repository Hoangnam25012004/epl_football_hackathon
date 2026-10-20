"""Real LLM tests (design section 13.7): opt-in with `uv run pytest -m live`.

The provider comes from backend/.env (or the environment); without one, the local Ollama
server is tried (`LIVE_PROVIDER=foundry` / `openai` to choose). Skipped when nothing is
configured or reachable. The 30-claim evaluation is long on a laptop CPU, so it only runs
with LIVE_EVAL=1 (or use `uv run pundit eval --provider ollama`). Never run in CI.
"""

import os
from functools import cache

import httpx
import pytest
from helpers import run, source

from pundit.agents.contracts import ClaimRequest
from pundit.cli import ENV_FILE, EVAL_FILES
from pundit.llm.settings import ConfigError, Settings, read_env_file
from pundit.orchestration.evaluation import UNAVAILABLE, llm_failures_of, load_eval, run_eval
from pundit.orchestration.service import ClaimService

pytestmark = pytest.mark.live


@cache
def live_settings() -> Settings | None:
    env = {**read_env_file(ENV_FILE), **os.environ}
    provider = os.environ.get("LIVE_PROVIDER") or (
        env.get("LLM_PROVIDER") if env.get("USE_MOCK_LLM", "").lower() == "false" else None
    )
    env.update({"USE_MOCK_LLM": "false", "LLM_PROVIDER": provider or "ollama"})
    try:
        settings = Settings.from_env(env)
    except ConfigError:
        return None
    if settings.provider == "ollama" and not reachable(settings):
        return None
    return settings


def reachable(settings: Settings) -> bool:
    assert settings.base_url is not None
    try:
        return httpx.get(f"{settings.base_url}/models", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture
def service() -> ClaimService:
    settings = live_settings()
    if settings is None:
        pytest.skip("no live LLM configured or reachable (Ollama, LLM_* or FOUNDRY_* settings)")
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
    # A small local model may disagree with the rules (the rules then win), answer twice with
    # an unusable spec or have its text rejected twice; it may not be unreachable or time out.
    failures = llm_failures_of(result)
    assert not failures & UNAVAILABLE, f"the real LLM was not available: {failures}"
    llm_calls = [s for s in result.trace or () if s.kind == "llm"]
    assert llm_calls and all(s.model == service.model for s in llm_calls)


def test_eval_with_the_real_model(service: ClaimService) -> None:
    if os.environ.get("LIVE_EVAL") != "1":
        pytest.skip("30 claims take long on a CPU: set LIVE_EVAL=1 or run `pundit eval`")
    report = run(run_eval([load_eval(path) for path in EVAL_FILES], service))
    print("\n".join(report.lines()))
    assert not report.wrong, [(r.case.id, r.result.verdict) for r in report.wrong]
    assert report.leaks == 0
    assert report.llm_working(), "the real LLM did not answer: only fallbacks were tested"
    assert report.accuracy >= 0.9
