"""LLM_LIVE_SCOPE (docs/day7-design.md section 2.2): "claims" keeps the live model to the claim
checker; the studio and the recaps write from their templates."""

import pytest

from pundit.llm.settings import ConfigError, Settings
from pundit.orchestration.recap_service import default_recap_writer
from pundit.orchestration.studio_service import default_studio_ports
from pundit.orchestration.studio_steps import StudioPorts

OLLAMA = {"USE_MOCK_LLM": "false", "LLM_PROVIDER": "ollama", "LLM_MODEL": "qwen2.5:1.5b"}


def test_claims_only_leaves_the_studio_and_the_recaps_to_the_templates() -> None:
    settings = Settings.from_env({**OLLAMA, "LLM_LIVE_SCOPE": "Claims"})
    assert settings.llm_live_scope == "claims" and settings.provider == "ollama"
    assert not settings.studio_uses_model
    assert default_studio_ports(settings)() == StudioPorts()  # no writer: template only
    assert default_recap_writer(settings)() is None  # the deterministic recap
    assert settings.describe()["llm_live_scope"] == "claims"


def test_all_is_the_default_and_gives_the_studio_its_model_writers() -> None:
    settings = Settings.from_env(OLLAMA)
    assert settings.llm_live_scope == "all" and settings.studio_uses_model
    ports = default_studio_ports(settings)()
    assert None not in (ports.talker, ports.commentator, ports.designer)
    assert default_recap_writer(settings)() is not None


def test_the_mock_keeps_its_writers_whatever_the_scope() -> None:
    settings = Settings.from_env({"LLM_LIVE_SCOPE": "claims"})
    assert settings.provider == "mock" and not settings.studio_uses_model
    assert default_studio_ports(settings)().talker is not None
    assert default_recap_writer(settings)() is not None


def test_an_unknown_scope_is_refused() -> None:
    with pytest.raises(ConfigError, match="LLM_LIVE_SCOPE must be all or claims"):
        Settings.from_env({"LLM_LIVE_SCOPE": "studio"})
