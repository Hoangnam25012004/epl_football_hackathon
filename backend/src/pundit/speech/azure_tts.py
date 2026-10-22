"""Azure AI Speech text to speech over REST (docs/day5-design.md section 8.3).

POST the SSML, get MP3 (24 kHz, 48 kbit/s, mono). REST instead of the Speech SDK: no native
library in the image, and every status code is testable with httpx.MockTransport. A 401 with
Entra ID refreshes the token once; other failures become typed errors for the Narrator.
"""

import asyncio
from typing import Literal

import httpx

from pundit.agents.contracts import AudioMediaType
from pundit.llm.settings import Settings
from pundit.speech.audio import MIN_MP3_BYTES, is_mp3, mp3_duration_ms
from pundit.speech.auth import SpeechAuth
from pundit.speech.ports import (
    BadAudio,
    SpeechAuthError,
    SpeechBadRequest,
    SpeechThrottled,
    SpeechTimeout,
    SpeechUnavailable,
    SynthesisRequest,
    SynthesizedAudio,
)

OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
USER_AGENT = "pundit-copilot/0.5"
CONNECT_TIMEOUT_S = 2.0
DEFAULT_RETRY_AFTER_S = 5.0


def tts_url(settings: Settings) -> str:
    """The custom domain with Entra ID (path to confirm on Azure, tier C), else the region."""
    if settings.speech_endpoint and settings.speech_resource_id:
        return f"{settings.speech_endpoint}/tts/cognitiveservices/v1"
    return f"https://{settings.speech_region}.tts.speech.microsoft.com/cognitiveservices/v1"


def retry_after(response: httpx.Response) -> float:
    try:
        value = float(response.headers.get("Retry-After", DEFAULT_RETRY_AFTER_S))
    except ValueError:
        value = DEFAULT_RETRY_AFTER_S
    return min(60.0, max(1.0, value))


class AzureSynthesizer:
    def __init__(
        self, url: str, auth: SpeechAuth, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.url = url
        self._auth = auth
        self._client = httpx.AsyncClient(
            transport=transport,
            headers={"User-Agent": USER_AGENT},
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )

    @property
    def name(self) -> Literal["azure"]:
        return "azure"

    @property
    def media_type(self) -> AudioMediaType:
        return "audio/mpeg"

    @property
    def output_format(self) -> str:
        return OUTPUT_FORMAT

    async def _post(self, request: SynthesisRequest, timeout_s: float) -> httpx.Response:
        headers = {
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
            **await self._auth.headers(),
        }
        timeout = httpx.Timeout(timeout_s, connect=min(CONNECT_TIMEOUT_S, timeout_s))
        try:
            async with asyncio.timeout(timeout_s):  # the whole call, not one read
                return await self._client.post(
                    self.url, content=request.ssml.encode("utf-8"), headers=headers,
                    timeout=timeout,
                )  # fmt: skip
        except (TimeoutError, httpx.TimeoutException):
            raise SpeechTimeout(f"no audio within {timeout_s:.2f} s") from None
        except httpx.HTTPError as exc:
            raise SpeechUnavailable(f"network error: {type(exc).__name__}") from None

    async def synthesize(self, request: SynthesisRequest, *, timeout_s: float) -> SynthesizedAudio:
        response = await self._post(request, timeout_s)
        if response.status_code == 401 and self._auth.mode == "entra":
            self._auth.invalidate()  # an expired token: one more try with a fresh one
            response = await self._post(request, timeout_s)
        return self._audio(response)

    def _audio(self, response: httpx.Response) -> SynthesizedAudio:
        status = response.status_code
        request_id = response.headers.get("X-RequestId")
        if status == 200:
            media = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            data = response.content
            if media != "audio/mpeg" or len(data) < MIN_MP3_BYTES or not is_mp3(data):
                raise BadAudio(f"HTTP 200 without MP3 audio ({media or 'no content type'})")
            return SynthesizedAudio(data, "audio/mpeg", mp3_duration_ms(data), "azure", request_id)
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
