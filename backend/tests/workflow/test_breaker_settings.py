"""LLM_BREAKER_COOLDOWN_S (docs/day6-design.md 5.7): the cooldown of the LLM breakers of the
claims, the studio and the recaps comes from the settings, 60 s by default (the old behaviour);
the voice keeps its own breaker."""

import pytest

from pundit.llm.settings import ConfigError, Settings
from pundit.orchestration.recap_service import RecapService
from pundit.orchestration.resilience import BREAKER_COOLDOWN_S, CircuitBreaker
from pundit.orchestration.service import ClaimService
from pundit.orchestration.studio_service import StudioService
from pundit.speech.narrator import build_narrator


def test_the_default_is_the_old_cooldown() -> None:
    settings = Settings.from_env({})
    assert settings.llm_breaker_cooldown_s == BREAKER_COOLDOWN_S == 60.0
    assert ClaimService(settings).breaker.cooldown_s == 60.0
    assert settings.describe()["llm_breaker_cooldown_s"] == 60.0


def test_every_llm_breaker_reads_the_setting() -> None:
    settings = Settings.from_env({"LLM_BREAKER_COOLDOWN_S": "7.5"})
    assert settings.llm_breaker_cooldown_s == 7.5
    assert ClaimService(settings).breaker.cooldown_s == 7.5
    assert StudioService(settings).breaker.cooldown_s == 7.5
    assert RecapService(settings).breaker.cooldown_s == 7.5


def test_the_voice_keeps_its_own_breaker() -> None:
    narrator = build_narrator(Settings(llm_breaker_cooldown_s=7.5, tts_provider="mock"))
    assert narrator is not None
    assert narrator.breaker.cooldown_s == BREAKER_COOLDOWN_S


@pytest.mark.parametrize("value", ["0", "-5", "soon"])
def test_a_bad_cooldown_is_refused(value: str) -> None:
    with pytest.raises(ConfigError, match="LLM_BREAKER_COOLDOWN_S"):
        Settings.from_env({"LLM_BREAKER_COOLDOWN_S": value})


def test_a_short_cooldown_lets_a_trial_call_through_sooner() -> None:
    now = [0.0]
    breaker = CircuitBreaker(cooldown_s=5.0, clock=lambda: now[0])
    for _ in range(breaker.threshold):
        breaker.record_failure()
    assert not breaker.allow()
    now[0] = 4.9
    assert not breaker.allow()
    now[0] = 5.1
    assert breaker.allow()  # half-open: one trial
