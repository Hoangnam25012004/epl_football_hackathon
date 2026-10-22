"""Mock synthesizer and recognizer (docs/day5-design.md sections 8.2 and 11): offline, no
secret, deterministic. The WAV lasts as long as the real voice would (times MOCK_TTS_TIME_SCALE)
so the fan player's queue behaves like it will with Azure; MOCK_TTS_FAULTS injects failures."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from pundit.agents.contracts import AudioMediaType, Lang, RecognitionResult
from pundit.llm.faults import TtsFaultCounter, TtsFaultPlan
from pundit.speech.audio import mock_wav
from pundit.speech.ports import (
    RecognitionRequest,
    SpeechAuthError,
    SpeechThrottled,
    SpeechTimeout,
    SpeechUnavailable,
    SynthesisRequest,
    SynthesizedAudio,
)

MOCK_OUTPUT_FORMAT = "riff-16khz-16bit-mono-pcm"
MIN_MOCK_MS = 50
MOCK_STT_TEXTS: dict[Lang, str] = {
    "vi": "Đội nhà áp đảo hiệp 2",
    "en": "The home side dominated the second half",
}
Sleep = Callable[[float], Awaitable[None]]


class MockSynthesizer:
    def __init__(
        self,
        *,
        time_scale: float = 1.0,
        latency_ms: float = 0.0,
        faults: TtsFaultPlan | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.time_scale = time_scale
        self.latency_s = latency_ms / 1000
        self._faults = TtsFaultCounter(faults or TtsFaultPlan())
        self._sleep = sleep
        self.calls = 0

    @property
    def name(self) -> Literal["mock"]:
        return "mock"

    @property
    def media_type(self) -> AudioMediaType:
        return "audio/wav"

    @property
    def output_format(self) -> str:
        return MOCK_OUTPUT_FORMAT

    async def synthesize(self, request: SynthesisRequest, *, timeout_s: float) -> SynthesizedAudio:
        self.calls += 1
        fault = self._faults.take()
        if fault == "timeout" or self.latency_s > timeout_s:
            await self._sleep(timeout_s)
            raise SpeechTimeout(f"mock: no audio within {timeout_s:.2f} s")
        if self.latency_s:
            await self._sleep(self.latency_s)
        if fault == "throttle":
            raise SpeechThrottled("mock: HTTP 429", retry_after_s=1.0)
        if fault == "auth":
            raise SpeechAuthError("mock: HTTP 401")
        if fault == "error":
            raise SpeechUnavailable("mock: HTTP 503")
        duration = max(MIN_MOCK_MS, round(request.estimate_ms * self.time_scale))
        if fault == "bad_audio":
            return SynthesizedAudio(b"this is not audio", "audio/wav", duration, "mock")
        tones = tuple(round(o * self.time_scale) for o in request.segment_offsets_ms) or (0,)
        return SynthesizedAudio(mock_wav(duration, tones), "audio/wav", duration, "mock")

    async def aclose(self) -> None:
        return None


class MockRecognizer:
    """Returns a fixed claim (MOCK_STT_TEXT) once the API has checked the WAV like Azure would."""

    def __init__(self, text: str | None = None) -> None:
        self._text = text
        self.calls = 0

    @property
    def name(self) -> Literal["mock"]:
        return "mock"

    async def recognize(
        self, request: RecognitionRequest, *, timeout_s: float
    ) -> RecognitionResult:
        del timeout_s
        self.calls += 1
        return RecognitionResult(
            text=self._text or MOCK_STT_TEXTS[request.language],
            language=request.language,
            status="ok",
            confidence=0.99,
            duration_ms=request.duration_ms,
            provider="mock",
        )

    async def aclose(self) -> None:
        return None
