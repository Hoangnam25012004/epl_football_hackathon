"""POST /speech/recognize (P2, docs/day5-design.md section 11): what the studio's microphone
sends and gets back, every refusal (413, 415, 422, 429, 503), and the app without speech to text
(the route is not there, the health says off)."""

import struct
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import api_settings

from pundit.agents.contracts import RecognitionResult
from pundit.api.app import create_app
from pundit.speech.mock import MOCK_STT_TEXTS, MockRecognizer
from pundit.speech.ports import RecognitionRequest, SpeechThrottled, SpeechUnavailable
from pundit.speech.transcriber import Transcriber

WAV_HEADERS = {"Content-Type": "audio/wav"}


def wav(ms: int, *, rate: int = 16_000, channels: int = 1) -> bytes:
    frame = channels * 2
    data = bytes(rate * ms // 1000 * frame)
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * frame, frame, 16)
    return header + b"data" + struct.pack("<I", len(data)) + data


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(api_settings(stt_provider="mock"))) as test_client:
        yield test_client


def test_a_spoken_claim_becomes_text_for_the_claim_box(client: TestClient) -> None:
    response = client.post("/speech/recognize?lang=vi", content=wav(2000), headers=WAV_HEADERS)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = RecognitionResult.model_validate(response.json())
    assert (body.text, body.status, body.language, body.provider, body.duration_ms) == (
        MOCK_STT_TEXTS["vi"], "ok", "vi", "mock", 2000,
    )  # fmt: skip
    english = client.post("/speech/recognize?lang=en", content=wav(800),
                          headers={"Content-Type": "audio/x-wav"})  # fmt: skip
    assert english.json()["text"] == MOCK_STT_TEXTS["en"]
    tap = client.post("/speech/recognize", content=wav(100), headers=WAV_HEADERS)
    assert tap.json()["status"] == "silence"  # a tap: no call, nothing to fill


@pytest.mark.parametrize(
    ("content", "headers", "status", "detail", "reason"),
    [
        (
            wav(1000),
            {"Content-Type": "application/octet-stream"},
            415,
            "unsupported_media_type",
            None,
        ),
        (wav(1000), {}, 415, "unsupported_media_type", None),
        (wav(1000, channels=2), WAV_HEADERS, 422, "bad_audio", "not_mono"),
        (wav(1000, rate=48_000), WAV_HEADERS, 422, "bad_audio", "not_16khz"),
        (wav(15_500), WAV_HEADERS, 422, "bad_audio", "too_long"),
        (b"RIFF....WAVE", WAV_HEADERS, 422, "bad_audio", "not_wav"),
        (wav(0), WAV_HEADERS, 422, "bad_audio", "empty"),
    ],
    ids=["octet", "none", "stereo", "48k", "long", "riff", "empty"],
)
def test_refusals(
    client: TestClient,
    content: bytes,
    headers: dict[str, str],
    status: int,
    detail: str,
    reason: str | None,
) -> None:
    response = client.post("/speech/recognize?lang=vi", content=content, headers=headers)
    assert response.status_code == status
    assert response.json().get("detail") == detail
    assert response.json().get("reason") == reason


def test_more_than_a_megabyte_is_refused_before_it_is_read(client: TestClient) -> None:
    big = client.post("/speech/recognize", content=bytes(1_000_001), headers=WAV_HEADERS)
    assert (big.status_code, big.json()) == (413, {"detail": "too_large"})

    def chunks() -> Iterator[bytes]:  # no Content-Length: counted while it streams
        for _ in range(20):
            yield bytes(64_000)

    streamed = client.post("/speech/recognize", content=chunks(), headers=WAV_HEADERS)
    assert streamed.status_code == 413


def test_a_language_the_studio_does_not_speak(client: TestClient) -> None:
    response = client.post("/speech/recognize?lang=fr", content=wav(1000), headers=WAV_HEADERS)
    assert response.status_code == 422


def test_ten_a_minute_then_429_with_retry_after(client: TestClient) -> None:
    for _ in range(10):
        ok = client.post("/speech/recognize", content=wav(1000), headers=WAV_HEADERS)
        assert ok.status_code == 200
    refused = client.post("/speech/recognize", content=wav(1000), headers=WAV_HEADERS)
    assert refused.status_code == 429
    assert refused.json() == {"detail": "stt_busy", "reason": "rate_limited"}
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    # a refused recording (422) never uses the minute: it is checked first
    bad = client.post("/speech/recognize", content=wav(1000, channels=2), headers=WAV_HEADERS)
    assert bad.status_code == 422


class Raising(MockRecognizer):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def recognize(
        self, request: RecognitionRequest, *, timeout_s: float
    ) -> RecognitionResult:
        raise self.error


@pytest.mark.parametrize(
    ("error", "status", "body", "retry"),
    [
        (
            SpeechUnavailable("HTTP 503"),
            503,
            {"detail": "stt_unavailable", "reason": "unavailable"},
            "2",
        ),
        (
            SpeechThrottled("HTTP 429", retry_after_s=7),
            429,
            {"detail": "stt_busy", "reason": "throttled"},
            "7",
        ),
    ],
    ids=["unavailable", "throttled"],
)
def test_the_recognizer_fails(
    error: Exception, status: int, body: dict[str, Any], retry: str
) -> None:
    settings = api_settings(stt_provider="mock")
    app = create_app(settings, transcriber=Transcriber(settings, Raising(error)))
    with TestClient(app) as test_client:
        response = test_client.post("/speech/recognize", content=wav(1000), headers=WAV_HEADERS)
    assert (response.status_code, response.json()) == (status, body)
    assert response.headers["Retry-After"] == retry


def test_without_speech_to_text_the_route_is_not_there() -> None:
    with TestClient(create_app(api_settings())) as test_client:  # STT_PROVIDER=off by default
        response = test_client.post("/speech/recognize", content=wav(1000), headers=WAV_HEADERS)
        assert response.status_code == 404
        speech = test_client.get("/healthz").json().get("speech")
        if speech is not None:  # the voice may be off too (TTS_PROVIDER=off)
            assert speech["stt"] == "off"


def test_the_health_says_which_recognizer(client: TestClient) -> None:
    speech = client.get("/healthz").json().get("speech")
    if speech is not None:
        assert speech["stt"] == "mock"
