"""Azure AI Speech, speech to text for short audio over REST (docs/day5-design.md section 11).

A spoken claim (at most STT_MAX_SECONDS of WAV PCM 16 kHz mono, checked by the API) becomes the
final text of the recognition. The language is given (short audio does not detect it). Like the
synthesizer: REST rather than the Speech SDK, a 401 with Entra ID refreshes the token once, and
every failure is a typed error. The audio is sent once and kept nowhere.
"""

import asyncio
from typing import Any, Literal

import httpx

from pundit.agents.contracts import Lang, RecognitionResult
from pundit.llm.settings import Settings
from pundit.speech.auth import SpeechAuth
from pundit.speech.azure_tts import CONNECT_TIMEOUT_S, USER_AGENT, retry_after
from pundit.speech.ports import (
    RecognitionRequest,
    SpeechAuthError,
    SpeechBadRequest,
    SpeechThrottled,
    SpeechTimeout,
    SpeechUnavailable,
)

STT_LOCALES: dict[Lang, str] = {"vi": "vi-VN", "en": "en-GB"}
SILENCE = {"InitialSilenceTimeout", "BabbleTimeout"}
STT_PATH = "speech/recognition/conversation/cognitiveservices/v1"


def stt_url(settings: Settings) -> str:
    """The custom domain with Entra ID, else the region's endpoint."""
    if settings.speech_endpoint and settings.speech_resource_id:
        return f"{settings.speech_endpoint}/stt/{STT_PATH}"
    return f"https://{settings.speech_region}.stt.speech.microsoft.com/{STT_PATH}"


def parse_recognition(body: Any, request: RecognitionRequest) -> RecognitionResult:
    """The JSON of a recognition (format=detailed) as a result; an `Error` status raises."""
    if not isinstance(body, dict):
        raise SpeechUnavailable("the recognition is not a JSON object")
    status = body.get("RecognitionStatus")

    def result(
        state: Literal["ok", "no_match", "silence"], text: str = "", confidence: float | None = None
    ) -> RecognitionResult:
        return RecognitionResult(
            text=text,
            language=request.language,
            status=state,
            confidence=confidence,
            duration_ms=request.duration_ms,
            provider="azure",
        )

    if status == "Success":
        best = body.get("NBest") or []
        first = best[0] if best and isinstance(best[0], dict) else {}
        text = str(first.get("Display") or body.get("DisplayText") or "").strip()
        confidence = first.get("Confidence")
        score = float(confidence) if isinstance(confidence, int | float) else None
        return result("ok", text, score) if text else result("no_match")
    if status == "NoMatch":
        return result("no_match")
    if status in SILENCE:
        return result("silence")
    raise SpeechUnavailable(f"recognition status {status!r}")


class AzureRecognizer:
    def __init__(
        self, url: str, auth: SpeechAuth, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.url = url
        self._auth = auth
        self._client = httpx.AsyncClient(
            transport=transport,
            headers={"User-Agent": USER_AGENT},
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
        )

    @property
    def name(self) -> Literal["azure"]:
        return "azure"

    async def _post(self, request: RecognitionRequest, timeout_s: float) -> httpx.Response:
        headers = {
            "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
            "Accept": "application/json",
            **await self._auth.headers(),
        }
        params = {
            "language": STT_LOCALES[request.language],
            "format": "detailed",
            "profanity": "masked",
        }
        timeout = httpx.Timeout(timeout_s, connect=min(CONNECT_TIMEOUT_S, timeout_s))
        try:
            async with asyncio.timeout(timeout_s):
                return await self._client.post(
                    self.url, params=params, content=request.wav, headers=headers,
                    timeout=timeout,
                )  # fmt: skip
        except (TimeoutError, httpx.TimeoutException):
            raise SpeechTimeout(f"no text within {timeout_s:.2f} s") from None
        except httpx.HTTPError as exc:
            raise SpeechUnavailable(f"network error: {type(exc).__name__}") from None

    async def recognize(
        self, request: RecognitionRequest, *, timeout_s: float
    ) -> RecognitionResult:
        response = await self._post(request, timeout_s)
        if response.status_code == 401 and self._auth.mode == "entra":
            self._auth.invalidate()
            response = await self._post(request, timeout_s)
        status = response.status_code
        if status == 200:
            try:
                body = response.json()
            except ValueError:
                raise SpeechUnavailable("HTTP 200 without JSON") from None
            return parse_recognition(body, request)
        request_id = response.headers.get("X-RequestId")
        detail = f"HTTP {status}" + (f" (request {request_id})" if request_id else "")
        if status in (400, 415):
            raise SpeechBadRequest(detail)
        if status in (401, 403):
            raise SpeechAuthError(detail)
        if status == 429:
            raise SpeechThrottled(detail, retry_after_s=retry_after(response))
        raise SpeechUnavailable(detail)

    async def aclose(self) -> None:
        await self._client.aclose()
