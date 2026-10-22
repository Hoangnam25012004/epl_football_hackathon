"""Speech to text of a spoken claim (P2, docs/day5-design.md section 11): the WAV the API accepts,
the Transcriber's limits (one call at a time, ten a minute, nothing for a tap), the mock, and
Azure's short-audio REST against httpx.MockTransport."""

import asyncio
import logging
import struct
from typing import Any

import httpx
import pytest
from helpers import run
from pydantic import SecretStr

from pundit.agents.contracts import RecognitionResult
from pundit.llm.settings import Settings
from pundit.speech.auth import EntraAuth, KeyAuth
from pundit.speech.azure_stt import AzureRecognizer, parse_recognition, stt_url
from pundit.speech.mock import MOCK_STT_TEXTS, MockRecognizer
from pundit.speech.ports import (
    RecognitionRequest,
    SpeechAuthError,
    SpeechBadRequest,
    SpeechThrottled,
    SpeechTimeout,
    SpeechUnavailable,
)
from pundit.speech.transcriber import (
    BadClaimAudio,
    SttRefused,
    Transcriber,
    build_transcriber,
    check_claim_wav,
)

URL = (
    "https://southeastasia.stt.speech.microsoft.com"
    "/speech/recognition/conversation/cognitiveservices/v1"
)


def wav(ms: int, *, rate: int = 16_000, channels: int = 1, bits: int = 16, fmt: int = 1) -> bytes:
    """A WAV the way a browser writes one (RIFF, fmt, data), silent."""
    frame = channels * bits // 8
    data = bytes(rate * ms // 1000 * frame)
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, fmt, channels, rate, rate * frame, frame, bits)
    return header + b"data" + struct.pack("<I", len(data)) + data


def settings(**values: Any) -> Settings:
    return Settings(stt_provider="mock", **values)


# ------------------------------------------------------------------------------ the WAV


def test_a_recording_the_recognizer_accepts() -> None:
    assert check_claim_wav(wav(2500), 15) == 2500
    assert check_claim_wav(wav(15_000), 15) == 15_000  # the limit itself


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        (b"not a wav at all" * 4, "not_wav"),
        (wav(1000, fmt=3, bits=32), "not_pcm"),
        (wav(1000, channels=2), "not_mono"),
        (wav(1000, rate=44_100), "not_16khz"),
        (wav(1000, rate=8_000), "not_16khz"),
        (wav(1000, bits=8), "not_16bit"),
        (wav(0), "empty"),
        (wav(15_100), "too_long"),
    ],
    ids=["riff", "float", "stereo", "44k", "8k", "8bit", "empty", "long"],
)
def test_recordings_the_api_refuses(data: bytes, problem: str) -> None:
    with pytest.raises(BadClaimAudio) as caught:
        check_claim_wav(data, 15)
    assert caught.value.problem == problem


# ------------------------------------------------------------------------------ Transcriber


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_mock_returns_the_claim_of_the_language() -> None:
    transcriber = Transcriber(settings(), MockRecognizer())
    vi = run(transcriber.recognize(wav(2000), "vi"))
    en = run(transcriber.recognize(wav(2000), "en"))
    assert (vi.text, vi.status, vi.provider, vi.duration_ms) == (
        MOCK_STT_TEXTS["vi"], "ok", "mock", 2000,
    )  # fmt: skip
    assert en.text == "The home side dominated the second half"
    assert run(Transcriber(settings(), MockRecognizer("Đội khách chuyền bóng tốt hơn"))
               .recognize(wav(900), "vi")).text == "Đội khách chuyền bóng tốt hơn"  # fmt: skip


def test_a_tap_costs_no_call() -> None:
    recognizer = MockRecognizer()
    transcriber = Transcriber(settings(), recognizer)
    result = run(transcriber.recognize(wav(120), "vi"))
    assert (result.status, result.text, recognizer.calls) == ("silence", "", 0)


def test_ten_calls_a_minute_for_the_whole_app() -> None:
    clock = Clock()
    recognizer = MockRecognizer()
    transcriber = Transcriber(settings(), recognizer, clock=clock)

    async def burst() -> None:
        for _ in range(10):
            await transcriber.recognize(wav(1000), "vi")
            clock.now += 1
        with pytest.raises(SttRefused) as caught:
            await transcriber.recognize(wav(1000), "vi")
        assert caught.value.reason == "rate_limited"
        assert caught.value.retry_after_s == pytest.approx(50.0)  # the oldest call ends in 50 s
        clock.now += 51  # the first call is a minute old: one more is allowed
        await transcriber.recognize(wav(1000), "vi")

    run(burst())
    assert recognizer.calls == 11 and transcriber.counts["limited"] == 1


class SlowRecognizer(MockRecognizer):
    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay = delay

    async def recognize(
        self, request: RecognitionRequest, *, timeout_s: float
    ) -> RecognitionResult:
        await asyncio.sleep(self.delay)
        return await super().recognize(request, timeout_s=timeout_s)


def test_one_call_at_a_time_and_a_busy_caller_uses_no_slot() -> None:
    recognizer = SlowRecognizer(0.3)
    transcriber = Transcriber(settings(), recognizer, busy_wait_s=0.05, per_minute=2)

    async def two_at_once() -> list[Any]:
        return await asyncio.gather(
            transcriber.recognize(wav(1000), "vi"),
            transcriber.recognize(wav(1000), "vi"),
            return_exceptions=True,
        )

    first, second = run(two_at_once())
    assert isinstance(first, RecognitionResult) and first.status == "ok"
    assert isinstance(second, SttRefused) and second.reason == "busy"
    # the busy caller did not use the minute: a second call is still allowed
    assert run(transcriber.recognize(wav(1000), "vi")).status == "ok"
    assert recognizer.calls == 2 and transcriber.counts["busy"] == 1


class FailingRecognizer(MockRecognizer):
    async def recognize(
        self, request: RecognitionRequest, *, timeout_s: float
    ) -> RecognitionResult:
        raise SpeechUnavailable("HTTP 503")


def test_a_failure_is_counted_and_never_logs_audio_or_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="pundit.speech")
    failing = Transcriber(settings(), FailingRecognizer())
    with pytest.raises(SpeechUnavailable):
        run(failing.recognize(wav(1000), "vi"))
    assert failing.counts["error"] == 1
    run(Transcriber(settings(), MockRecognizer()).recognize(wav(1000), "vi"))
    lines = [record.getMessage() for record in caplog.records]
    assert any("speech.recognize lang=vi audio_ms=1000" in line and "status=ok" in line
               for line in lines)  # fmt: skip
    assert all(MOCK_STT_TEXTS["vi"] not in line for line in lines)


def test_build_transcriber_follows_stt_provider() -> None:
    assert build_transcriber(Settings()) is None  # off by default
    mock = build_transcriber(settings())
    assert mock is not None and mock.provider == "mock"
    keyed = build_transcriber(
        Settings(stt_provider="azure", speech_key=SecretStr("k"), speech_region="southeastasia")
    )
    assert keyed is not None and keyed.provider == "azure"
    run(keyed.aclose())
    with pytest.raises(ValueError, match="key or Entra ID"):
        build_transcriber(Settings(stt_provider="azure"))


# ------------------------------------------------------------------------------ Azure


REQUEST = RecognitionRequest(wav=wav(1500), language="vi", duration_ms=1500)


def recognizer(handler: Any, auth: Any = None) -> AzureRecognizer:
    return AzureRecognizer(
        URL, auth or KeyAuth(SecretStr("k-456")), transport=httpx.MockTransport(handler)
    )


def detailed(text: str, confidence: float = 0.91) -> dict[str, Any]:
    return {
        "RecognitionStatus": "Success",
        "Offset": 1_200_000,
        "Duration": 13_000_000,
        "NBest": [
            {"Confidence": confidence, "Lexical": text.lower(), "ITN": text.lower(),
             "MaskedITN": text.lower(), "Display": text},
        ],
    }  # fmt: skip


def test_the_request_azure_expects() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=detailed("Đội nhà áp đảo hiệp 2."))

    result = run(recognizer(handler).recognize(REQUEST, timeout_s=2))
    request = seen[0]
    assert request.method == "POST" and str(request.url).startswith(URL + "?")
    assert dict(request.url.params) == {
        "language": "vi-VN", "format": "detailed", "profanity": "masked",
    }  # fmt: skip
    assert request.headers["Content-Type"] == "audio/wav; codecs=audio/pcm; samplerate=16000"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Ocp-Apim-Subscription-Key"] == "k-456"
    assert request.content == REQUEST.wav
    assert (result.text, result.status, result.confidence, result.provider) == (
        "Đội nhà áp đảo hiệp 2.", "ok", 0.91, "azure",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("body", "status", "text"),
    [
        ({"RecognitionStatus": "Success", "DisplayText": "Hello."}, "ok", "Hello."),
        ({"RecognitionStatus": "Success", "NBest": [{"Display": "  "}]}, "no_match", ""),
        ({"RecognitionStatus": "NoMatch"}, "no_match", ""),
        ({"RecognitionStatus": "InitialSilenceTimeout"}, "silence", ""),
        ({"RecognitionStatus": "BabbleTimeout"}, "silence", ""),
    ],
    ids=["simple", "blank", "nomatch", "silence", "babble"],
)
def test_recognition_statuses(body: dict[str, Any], status: str, text: str) -> None:
    result = parse_recognition(body, REQUEST)
    assert (result.status, result.text, result.language) == (status, text, "vi")


@pytest.mark.parametrize("body", [{"RecognitionStatus": "Error"}, ["not", "an", "object"], {}])
def test_an_error_of_the_service_is_unavailable(body: Any) -> None:
    with pytest.raises(SpeechUnavailable):
        parse_recognition(body, REQUEST)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (400, SpeechBadRequest),
        (401, SpeechAuthError),
        (403, SpeechAuthError),
        (415, SpeechBadRequest),
        (429, SpeechThrottled),
        (500, SpeechUnavailable),
        (503, SpeechUnavailable),
    ],
)
def test_status_codes(status: int, error: type) -> None:
    with pytest.raises(error) as caught:
        run(recognizer(lambda _: httpx.Response(status)).recognize(REQUEST, timeout_s=2))
    assert "k-456" not in str(caught.value)


def test_not_json_timeouts_and_network_errors() -> None:
    def text(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>")

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json=detailed("late"))

    with pytest.raises(SpeechUnavailable):
        run(recognizer(text).recognize(REQUEST, timeout_s=2))
    with pytest.raises(SpeechTimeout):
        run(recognizer(timeout).recognize(REQUEST, timeout_s=2))
    with pytest.raises(SpeechUnavailable):
        run(recognizer(refused).recognize(REQUEST, timeout_s=2))
    with pytest.raises(SpeechTimeout):
        run(recognizer(slow).recognize(REQUEST, timeout_s=0.05))


class FakeCredential:
    def __init__(self) -> None:
        self.calls = 0

    def get_token(self, scope: str) -> Any:
        self.calls += 1
        return type("Token", (), {"token": f"t{self.calls}", "expires_on": 10**10})()


def test_entra_refreshes_the_token_once_on_401() -> None:
    credential = FakeCredential()
    auth = EntraAuth("/subscriptions/s/accounts/speech1", credential_factory=lambda: credential)
    answers = [httpx.Response(401), httpx.Response(200, json=detailed("Đúng."))]
    tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tokens.append(request.headers["Authorization"])
        return answers.pop(0)

    assert run(recognizer(handler, auth).recognize(REQUEST, timeout_s=2)).text == "Đúng."
    assert tokens == [
        "Bearer aad#/subscriptions/s/accounts/speech1#t1",
        "Bearer aad#/subscriptions/s/accounts/speech1#t2",
    ]


def test_urls_and_english() -> None:
    key = Settings(stt_provider="azure", speech_key=SecretStr("k"), speech_region="southeastasia")
    assert stt_url(key) == URL
    entra = Settings(
        stt_provider="azure",
        speech_endpoint="https://pundit-speech.cognitiveservices.azure.com",
        speech_resource_id="/subscriptions/s/accounts/pundit-speech",
    )
    assert stt_url(entra) == (
        "https://pundit-speech.cognitiveservices.azure.com/stt/speech/recognition/"
        "conversation/cognitiveservices/v1"
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=detailed("The home side dominated."))

    english = RecognitionRequest(wav=wav(1000), language="en", duration_ms=1000)
    assert run(recognizer(handler).recognize(english, timeout_s=2)).language == "en"
    assert seen[0].url.params["language"] == "en-GB"
