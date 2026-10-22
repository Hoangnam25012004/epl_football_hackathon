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
    settings = Settings.from_env({"LLM_PROVIDER": "ollama"})
    assert settings.provider == "mock" and settings.base_url is None


def test_ollama_provider_has_local_defaults() -> None:
    settings = Settings.from_env({"USE_MOCK_LLM": "false", "LLM_PROVIDER": "Ollama"})
    assert settings.provider == "ollama"
    assert settings.base_url == "http://127.0.0.1:11434/v1"
    assert settings.model_name == "qwen2.5:3b"
    assert (settings.llm_timeout_s, settings.claim_budget_s) == (90.0, 200.0)  # CPU model
    custom = Settings.from_env(
        {
            "USE_MOCK_LLM": "false",
            "LLM_PROVIDER": "ollama",
            "LLM_MODEL": "qwen2.5:7b",
            "LLM_BASE_URL": "http://gpu-box:11434/v1",
            "LLM_TIMEOUT_S": "30",
        }
    )
    assert (custom.model_name, custom.base_url, custom.llm_timeout_s) == (
        "qwen2.5:7b",
        "http://gpu-box:11434/v1",
        30.0,
    )


def test_openai_compatible_provider() -> None:
    settings = Settings.from_env(
        {
            "USE_MOCK_LLM": "false",
            "LLM_PROVIDER": "openai",
            "LLM_BASE_URL": "https://llm.example.test/v1",
            "LLM_MODEL": "some-model",
            "LLM_API_KEY": "sk-fake-secret-123",
        }
    )
    assert (settings.provider, settings.model_name) == ("openai", "some-model")
    assert (settings.llm_timeout_s, settings.claim_budget_s) == (4.0, 8.0)
    assert "sk-fake-secret-123" not in repr(settings)
    assert "sk-fake-secret-123" not in str(settings.describe())


def test_github_models_is_retired() -> None:
    with pytest.raises(ConfigError, match="retired"):
        Settings.from_env({"USE_MOCK_LLM": "false", "LLM_PROVIDER": "github"})


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
        ({"USE_MOCK_LLM": "false", "LLM_PROVIDER": "openai"}, "LLM_BASE_URL"),
        ({"USE_MOCK_LLM": "false", "LLM_PROVIDER": "openai"}, "LLM_MODEL"),
        ({"USE_MOCK_LLM": "false", "LLM_PROVIDER": "chatgpt"}, "ollama, openai or foundry"),
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


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-16-be"])
def test_env_files_written_on_windows(tmp_path: Path, encoding: str) -> None:
    """Notepad adds a BOM, PowerShell 5.1 `>` writes UTF-16: the first key must survive."""
    env_file = tmp_path / ".env"
    text = "USE_MOCK_LLM=false\r\nLLM_PROVIDER=ollama\r\n"
    data = text.encode(encoding)
    if encoding == "utf-16-be":
        data = b"\xfe\xff" + data
    env_file.write_bytes(data)
    assert read_env_file(env_file) == {"USE_MOCK_LLM": "false", "LLM_PROVIDER": "ollama"}


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
    assert "LLM_API_KEY" in values and values["LLM_API_KEY"] == ""  # names only, no secret
    assert "GITHUB_TOKEN" not in values
    for secret in ("SPEECH_KEY", "AUDIO_BLOB_CONNECTION_STRING", "FOUNDRY_API_KEY"):
        assert values[secret] == "", secret
    settings = Settings.from_env(values)
    assert settings.provider == "mock"
    assert (settings.tts_provider, settings.stt_provider, settings.audio_store) == (
        "mock",
        "off",
        "local",
    )
