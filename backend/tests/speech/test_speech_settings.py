"""Day 5 configuration (docs/day5-design.md section 3.7): defaults, every problem reported at
start-up, secrets kept out of repr and of the safe summary."""

import pytest

from pundit.llm.settings import ConfigError, Settings


def test_defaults_are_the_mock_voice_in_vietnamese() -> None:
    settings = Settings.from_env({})
    assert settings.tts_provider == "mock" and settings.tts_languages == ("vi",)
    assert settings.audio_store == "local" and settings.stt_provider == "off"
    assert settings.rate_per_minute == 60_000  # the mock has no real limit
    assert settings.speech_enabled and settings.cache_dir.name == "pundit-audio"
    assert Settings.from_env({"TTS_PROVIDER": "AZURE", "SPEECH_KEY": "k",
                              "SPEECH_REGION": "southeastasia"}).rate_per_minute == 18  # fmt: skip
    assert Settings.from_env({"TTS_MAX_RPM": "300"}).rate_per_minute == 300
    assert not Settings.from_env({"TTS_PROVIDER": "off"}).speech_enabled


@pytest.mark.parametrize(
    ("env", "problem"),
    [
        ({"TTS_PROVIDER": "polly"}, "TTS_PROVIDER must be"),
        ({"STT_PROVIDER": "whisper"}, "STT_PROVIDER must be"),
        ({"TTS_LANGUAGES": "vi,en"}, "'en' has no spoken pack"),
        ({"TTS_PROVIDER": "azure"}, "Azure Speech needs"),
        ({"STT_PROVIDER": "azure", "SPEECH_KEY": "k"}, "Azure Speech needs"),
        ({"AUDIO_STORE": "blob"}, "AUDIO_STORE=blob needs"),
        ({"AUDIO_STORE": "s3"}, "AUDIO_STORE must be"),
        ({"MOCK_TTS_FAULTS": "explode"}, "MOCK_TTS_FAULTS"),
        ({"TTS_MAX_RPM": "0"}, "TTS_MAX_RPM must be between"),
        ({"TTS_TIMEOUT_S": "-1"}, "TTS_TIMEOUT_S must be > 0"),
    ],
)
def test_problems_are_reported_at_start_up(env: dict[str, str], problem: str) -> None:
    with pytest.raises(ConfigError) as caught:
        Settings.from_env(env)
    assert problem in str(caught.value)


def test_azure_with_a_key_or_with_entra() -> None:
    key = Settings.from_env(
        {"TTS_PROVIDER": "azure", "SPEECH_KEY": "secret-key", "SPEECH_REGION": "southeastasia"}
    )
    assert key.speech_key is not None and "secret-key" not in repr(key)
    assert "secret-key" not in str(key.describe())
    entra = Settings.from_env(
        {
            "TTS_PROVIDER": "azure",
            "SPEECH_ENDPOINT": "https://pundit.cognitiveservices.azure.com/",
            "SPEECH_RESOURCE_ID": "/subscriptions/s/accounts/pundit",
            "AUDIO_STORE": "blob",
            "AUDIO_BLOB_ACCOUNT_URL": "https://punditaudio.blob.core.windows.net",
            "STT_PROVIDER": "azure",
        }
    )
    assert entra.speech_endpoint == "https://pundit.cognitiveservices.azure.com"  # no slash
    assert entra.describe()["speech"] == {
        "tts_provider": "azure", "tts_languages": ["vi"], "audio_store": "blob",
        "stt_provider": "azure", "mock_tts_faults": None,
    }  # fmt: skip


def test_mock_options_and_bounds() -> None:
    settings = Settings.from_env(
        {"MOCK_TTS_LATENCY_MS": "0", "MOCK_TTS_TIME_SCALE": "50", "STT_MAX_SECONDS": "300",
         "TTS_DAILY_CHAR_BUDGET": "0", "MOCK_TTS_FAULTS": "timeout:2,error"}
    )  # fmt: skip
    assert settings.mock_tts_latency_ms == 0 and settings.mock_tts_time_scale == 10
    assert settings.stt_max_seconds == 60 and settings.tts_daily_char_budget == 0
    assert settings.mock_tts_faults == "timeout:2,error"
