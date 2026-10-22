"""Transcriber (P2, docs/day5-design.md section 11): a commentator speaks a claim, the studio's
claim box gets the text. The commentator reads it and sends it: the text is never verified on
its own (a person decides).

The WAV is checked before anything else (PCM, mono, 16 kHz, 16-bit, at most STT_MAX_SECONDS).
Azure's free tier serves one recognition at a time: one call at a time here too, a second caller
waits at most BUSY_WAIT_S. The whole app makes at most STT_PER_MINUTE calls a minute (a public
demo is not a free speech-to-text service). A recording too short to hold a word costs no call.
The audio and the text are never stored or logged: the log has the length, the status, the time.
"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Literal

from pundit.agents.contracts import Lang, RecognitionResult
from pundit.domain.errors import PunditError
from pundit.llm.settings import Settings
from pundit.speech.audio import WAV_RATE, parse_wav
from pundit.speech.ports import RecognitionRequest, Recognizer

log = logging.getLogger("pundit.speech")

MAX_WAV_BYTES = 1_000_000
BUSY_WAIT_S = 5.0
STT_PER_MINUTE = 10
STT_TIMEOUT_S = 10.0
MIN_SPEECH_MS = 300  # shorter: a tap on the button, not a claim

WavProblem = Literal[
    "not_wav", "not_pcm", "not_mono", "not_16khz", "not_16bit", "empty", "too_long"
]


class BadClaimAudio(PunditError):
    """A recording the API refuses (422); `problem` says why."""

    def __init__(self, problem: WavProblem, message: str) -> None:
        super().__init__(message)
        self.problem: WavProblem = problem


class SttRefused(PunditError):
    """Not now (429): another recognition is running, or the app's minute is used up."""

    def __init__(self, reason: Literal["busy", "rate_limited"], retry_after_s: float) -> None:
        super().__init__(reason)
        self.reason: Literal["busy", "rate_limited"] = reason
        self.retry_after_s = retry_after_s


def check_claim_wav(data: bytes, max_seconds: float) -> int:
    """The duration (ms) of a recording the recognizer accepts; BadClaimAudio otherwise."""
    try:
        info = parse_wav(data)
    except ValueError as exc:
        problem: WavProblem = "not_pcm" if str(exc) == "not PCM" else "not_wav"
        raise BadClaimAudio(problem, str(exc)) from None
    if info.channels != 1:
        raise BadClaimAudio("not_mono", f"{info.channels} channels, expected 1")
    if info.sample_rate != WAV_RATE:
        raise BadClaimAudio("not_16khz", f"{info.sample_rate} Hz, expected {WAV_RATE}")
    if info.bits != 16:
        raise BadClaimAudio("not_16bit", f"{info.bits}-bit samples, expected 16")
    if info.data_bytes == 0:
        raise BadClaimAudio("empty", "no samples")
    if info.duration_ms > max_seconds * 1000:
        raise BadClaimAudio("too_long", f"{info.duration_ms} ms, at most {max_seconds:g} s")
    return info.duration_ms


class Transcriber:
    def __init__(
        self,
        settings: Settings,
        recognizer: Recognizer,
        *,
        clock: Callable[[], float] = time.monotonic,
        per_minute: int = STT_PER_MINUTE,
        busy_wait_s: float = BUSY_WAIT_S,
    ) -> None:
        self.settings = settings
        self.recognizer = recognizer
        self._clock = clock
        self._per_minute = per_minute
        self._busy_wait_s = busy_wait_s
        self._calls: deque[float] = deque()  # start times of the calls of the last minute
        self._one = asyncio.Semaphore(1)
        self.counts = {"ok": 0, "no_match": 0, "silence": 0, "error": 0, "busy": 0, "limited": 0}

    @property
    def provider(self) -> Literal["mock", "azure"]:
        return self.recognizer.name

    def _take_slot(self) -> None:
        now = self._clock()
        while self._calls and now - self._calls[0] >= 60.0:
            self._calls.popleft()
        if len(self._calls) >= self._per_minute:
            self.counts["limited"] += 1
            raise SttRefused("rate_limited", max(1.0, 60.0 - (now - self._calls[0])))
        self._calls.append(now)

    async def recognize(self, wav: bytes, language: Lang) -> RecognitionResult:
        duration_ms = check_claim_wav(wav, self.settings.stt_max_seconds)
        if duration_ms < MIN_SPEECH_MS:
            self.counts["silence"] += 1
            return RecognitionResult(text="", language=language, status="silence",
                                     duration_ms=duration_ms, provider=self.provider)  # fmt: skip
        try:
            await asyncio.wait_for(self._one.acquire(), timeout=self._busy_wait_s)
        except TimeoutError:
            self.counts["busy"] += 1
            raise SttRefused("busy", 2.0) from None
        try:
            self._take_slot()  # inside the one slot: a refused caller never uses the minute
            return await self._call(wav, language, duration_ms)
        finally:
            self._one.release()

    async def _call(self, wav: bytes, language: Lang, duration_ms: int) -> RecognitionResult:
        began = self._clock()
        status: Literal["ok", "no_match", "silence", "error"] = "error"
        try:
            result = await self.recognizer.recognize(
                RecognitionRequest(wav=wav, language=language, duration_ms=duration_ms),
                timeout_s=STT_TIMEOUT_S,
            )
            status = result.status
            return result
        finally:
            self.counts[status] += 1
            log.info(
                "speech.recognize lang=%s audio_ms=%d ms=%.0f status=%s provider=%s",
                language, duration_ms, (self._clock() - began) * 1000, status, self.provider,
            )  # fmt: skip

    async def aclose(self) -> None:
        await self.recognizer.aclose()


def build_transcriber(settings: Settings) -> Transcriber | None:
    """The app's Transcriber, or None when speech to text is off (STT_PROVIDER=off)."""
    if settings.stt_provider == "mock":
        from pundit.speech.mock import MockRecognizer

        return Transcriber(settings, MockRecognizer(settings.mock_stt_text))
    if settings.stt_provider == "azure":
        from pundit.speech.auth import build_auth
        from pundit.speech.azure_stt import AzureRecognizer, stt_url

        auth = build_auth(settings)
        if auth is None:
            raise ValueError("STT_PROVIDER=azure needs a key or Entra ID settings")
        return Transcriber(settings, AzureRecognizer(stt_url(settings), auth))
    return None
