"""Azure text to speech over REST, against httpx.MockTransport (docs/day5-design.md 8.3, 8.4):
the request Azure expects, the audio it returns, and every status code."""

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from helpers import run
from pydantic import SecretStr

from pundit.llm.settings import Settings
from pundit.speech.auth import EntraAuth, KeyAuth, build_auth
from pundit.speech.azure_tts import OUTPUT_FORMAT, AzureSynthesizer, tts_url
from pundit.speech.ports import (
    BadAudio,
    SpeechAuthError,
    SpeechBadRequest,
    SpeechThrottled,
    SpeechTimeout,
    SpeechUnavailable,
    SynthesisRequest,
)

URL = "https://southeastasia.tts.speech.microsoft.com/cognitiveservices/v1"
SSML = '<speak version="1.0" xml:lang="vi-VN"><voice name="vi-VN-HoaiMyNeural">Vào!</voice></speak>'
REQUEST = SynthesisRequest(
    clip_id="a" * 32, ssml=SSML, voice="vi-VN-HoaiMyNeural", lang="vi-VN", kind="commentary",
    priority=3, billable_chars=10, estimate_ms=1000,
)  # fmt: skip
MP3 = b"\xff\xfb\x90\x00" + bytes(11_996)  # two seconds at 48 kbit/s


def synthesizer(handler: Any, auth: Any = None) -> AzureSynthesizer:
    return AzureSynthesizer(
        URL, auth or KeyAuth(SecretStr("k-123")), transport=httpx.MockTransport(handler)
    )


def audio_response(content: bytes = MP3, media: str = "audio/mpeg") -> httpx.Response:
    return httpx.Response(200, content=content, headers={"Content-Type": media,
                                                          "X-RequestId": "r1"})  # fmt: skip


def test_the_request_azure_expects() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return audio_response()

    audio = run(synthesizer(handler).synthesize(REQUEST, timeout_s=2))
    request = seen[0]
    assert request.method == "POST" and str(request.url) == URL
    assert request.headers["Content-Type"] == "application/ssml+xml"
    assert request.headers["X-Microsoft-OutputFormat"] == OUTPUT_FORMAT
    assert request.headers["User-Agent"].startswith("pundit-copilot/")
    assert request.headers["Ocp-Apim-Subscription-Key"] == "k-123"
    assert request.content.decode("utf-8") == SSML
    assert (audio.media_type, audio.duration_ms, audio.provider, audio.request_id) == (
        "audio/mpeg", 2000, "azure", "r1",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("status", "headers", "error", "retry"),
    [
        (400, {}, SpeechBadRequest, None),
        (401, {}, SpeechAuthError, None),
        (403, {}, SpeechAuthError, None),
        (415, {}, SpeechBadRequest, None),
        (429, {"Retry-After": "7"}, SpeechThrottled, 7.0),
        (429, {}, SpeechThrottled, 5.0),
        (429, {"Retry-After": "soon"}, SpeechThrottled, 5.0),
        (500, {}, SpeechUnavailable, None),
        (502, {}, SpeechUnavailable, None),
        (503, {}, SpeechUnavailable, None),
    ],
)
def test_status_codes(status: int, headers: dict[str, str], error: type, retry: Any) -> None:
    synth = synthesizer(lambda _: httpx.Response(status, headers=headers))
    with pytest.raises(error) as caught:
        run(synth.synthesize(REQUEST, timeout_s=2))
    assert caught.value.retry_after_s == retry
    assert "k-123" not in str(caught.value)


@pytest.mark.parametrize(
    ("content", "media"),
    [(MP3, "audio/wav"), (b"\xff\xfb" + bytes(10), "audio/mpeg"), (b"<html>" * 400, "audio/mpeg")],
    ids=["wrong-content-type", "too-short", "not-mp3"],  # short ids (Windows env var limit)
)
def test_ok_without_mp3_is_bad_audio(content: bytes, media: str) -> None:
    with pytest.raises(BadAudio):
        run(synthesizer(lambda _: audio_response(content, media)).synthesize(REQUEST, timeout_s=2))


def test_timeouts_and_network_errors() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return audio_response()

    with pytest.raises(SpeechTimeout):
        run(synthesizer(timeout).synthesize(REQUEST, timeout_s=2))
    with pytest.raises(SpeechUnavailable):
        run(synthesizer(refused).synthesize(REQUEST, timeout_s=2))
    with pytest.raises(SpeechTimeout):  # the whole call is bounded, not one read
        run(synthesizer(slow).synthesize(REQUEST, timeout_s=0.05))


@dataclass
class Token:
    token: str
    expires_on: int


class FakeCredential:
    def __init__(self) -> None:
        self.calls = 0

    def get_token(self, scope: str) -> Token:
        assert scope == "https://cognitiveservices.azure.com/.default"
        self.calls += 1
        return Token(f"t{self.calls}", expires_on=1_000 + 3600)


def test_entra_header_cache_refresh_and_401() -> None:
    credential = FakeCredential()
    now = [1_000.0]
    auth = EntraAuth("/subscriptions/s/accounts/speech1", credential_factory=lambda: credential,
                     wall_clock=lambda: now[0])  # fmt: skip
    headers = run(auth.headers())
    assert headers == {"Authorization": "Bearer aad#/subscriptions/s/accounts/speech1#t1"}
    run(auth.headers())
    assert credential.calls == 1  # cached
    now[0] = 1_000 + 3600 - 299  # five minutes before it expires: refreshed
    assert run(auth.headers())["Authorization"].endswith("#t2")
    statuses = [401, 200]
    tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tokens.append(request.headers["Authorization"])
        status = statuses.pop(0)
        return audio_response() if status == 200 else httpx.Response(401)

    audio = run(synthesizer(handler, auth).synthesize(REQUEST, timeout_s=2))
    assert audio.duration_ms == 2000 and tokens[0] != tokens[1]  # a fresh token after 401
    assert "t3" not in repr(auth) and "speech1" in repr(auth)


def test_urls_and_auth_from_settings() -> None:
    key = Settings(tts_provider="azure", speech_key=SecretStr("k"), speech_region="southeastasia")
    assert tts_url(key) == URL and isinstance(build_auth(key), KeyAuth)
    entra = Settings(
        tts_provider="azure",
        speech_endpoint="https://pundit-speech.cognitiveservices.azure.com",
        speech_resource_id="/subscriptions/s/accounts/pundit-speech",
    )
    assert tts_url(entra) == (
        "https://pundit-speech.cognitiveservices.azure.com/tts/cognitiveservices/v1"
    )
    assert isinstance(build_auth(entra), EntraAuth)
    assert build_auth(Settings()) is None
    assert repr(KeyAuth(SecretStr("secret-key"))) == "KeyAuth(***)"
