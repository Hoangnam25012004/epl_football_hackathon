"""Configuration (design section 8.4) and fault plans (section 8.2)."""

from pathlib import Path

import pytest

from pundit.llm.faults import FaultPlan
from pundit.llm.settings import ConfigError, Settings, load_settings, read_env_file


def test_defaults_are_mock() -> None:
    settings = Settings.from_env({})
    assert settings.provider == "mock" and settings.model_name == "mock"
    assert (settings.llm_timeout_s, settings.claim_budget_s) == (4.0, 8.0)
    assert settings.cors_origins == ("http://localhost:3000",)


def test_mock_ignores_provider_settings() -> None:
    settings = Settings.from_env({"LLM_PROVIDER": "github"})
    assert settings.provider == "mock"


def test_github_provider() -> None:
    settings = Settings.from_env(
        {"USE_MOCK_LLM": "false", "LLM_PROVIDER": "GitHub", "GITHUB_TOKEN": "ghp_fake_secret_123"}
    )
    assert settings.provider == "github"
    assert settings.model_name == "openai/gpt-4.1-mini"
    assert "ghp_fake_secret_123" not in repr(settings)
    assert "ghp_fake_secret_123" not in str(settings.describe())


def test_foundry_provider() -> None:
    settings = Settings.from_env(
        {
            "USE_MOCK_LLM": "0",
            "LLM_PROVIDER": "foundry",
            "FOUNDRY_ENDPOINT": "https://res.openai.azure.com",
            "FOUNDRY_MODEL_DEPLOYMENT": "gpt-4.1-mini",
            "CORS_ORIGINS": "http://a.test, http://b.test",
        }
    )
    assert (settings.provider, settings.model_name) == ("foundry", "gpt-4.1-mini")
    assert settings.cors_origins == ("http://a.test", "http://b.test")


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"USE_MOCK_LLM": "false"}, "LLM_PROVIDER"),
        ({"USE_MOCK_LLM": "false", "LLM_PROVIDER": "github"}, "GITHUB_TOKEN"),
        ({"USE_MOCK_LLM": "false", "LLM_PROVIDER": "foundry"}, "FOUNDRY_ENDPOINT"),
        ({"USE_MOCK_LLM": "maybe"}, "USE_MOCK_LLM"),
        ({"LLM_TIMEOUT_S": "fast"}, "LLM_TIMEOUT_S"),
        ({"CLAIM_BUDGET_S": "0"}, "CLAIM_BUDGET_S"),
        ({"MOCK_LLM_FAULTS": "explainer:explode"}, "MOCK_LLM_FAULTS"),
    ],
)
def test_bad_configuration_fails_fast(env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(env)


def test_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nexport LLM_TIMEOUT_S=3\nMOCK_LLM_FAULTS='explainer:hallucinate:2'\n\nBROKEN\n",
        encoding="utf-8",
    )
    assert read_env_file(env_file) == {
        "LLM_TIMEOUT_S": "3",
        "MOCK_LLM_FAULTS": "explainer:hallucinate:2",
    }
    assert read_env_file(tmp_path / "missing.env") == {}
    for name in (
        "USE_MOCK_LLM",
        "LLM_PROVIDER",
        "MOCK_LLM_FAULTS",
        "CLAIM_BUDGET_S",
        "CORS_ORIGINS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_TIMEOUT_S", "2")  # the environment wins over the file
    settings = load_settings(env_file)
    assert settings.llm_timeout_s == 2 and settings.mock_llm_faults == "explainer:hallucinate:2"


def test_fault_plan_counts_per_claim() -> None:
    plan = FaultPlan.parse("analyst:timeout:1, explainer:hallucinate:2,explainer:unsafe")
    counter = plan.counter()
    assert counter.take("analyst") == "timeout"
    assert counter.take("analyst") is None
    assert [counter.take("explainer") for _ in range(4)] == [
        "hallucinate",
        "hallucinate",
        "unsafe",
        None,
    ]
    fresh = plan.counter()  # every claim starts again
    assert fresh.take("analyst") == "timeout"


@pytest.mark.parametrize(
    "text",
    [
        "analyst",
        "critic:timeout",
        "analyst:hallucinate",
        "explainer:wrong_entity",
        "analyst:error:0",
        "analyst:error:x",
    ],
)
def test_bad_fault_plans(text: str) -> None:
    with pytest.raises(ValueError):
        FaultPlan.parse(text)


def test_empty_fault_plan() -> None:
    assert FaultPlan.parse("").faults == () and FaultPlan.parse(" , ").faults == ()


def test_env_example_is_a_valid_mock_configuration() -> None:
    example = Path(__file__).resolve().parents[2] / ".env.example"
    values = read_env_file(example)
    assert "GITHUB_TOKEN" in values and values["GITHUB_TOKEN"] == ""  # names only, no secret
    assert Settings.from_env(values).provider == "mock"
