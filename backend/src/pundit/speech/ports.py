"""Ports of the voice (docs/day5-design.md sections 8.1 and 11): what the Narrator asks a
synthesizer, what the Transcriber asks a recognizer, and the typed errors they raise."""

from dataclasses import dataclass
from typing import Literal, Protocol

from pundit.agents.contracts import AudioMediaType, Lang, RecognitionResult
from pundit.domain.errors import PunditError

SynthesisKind = Literal["commentary", "recap", "greeting"]
SpeechErrorKind = Literal["timeout", "throttled", "auth", "bad_request", "unavailable", "bad_audio"]


@dataclass(frozen=True)
class SynthesisRequest:
    clip_id: str
    ssml: str
    voice: str
    lang: str
    kind: SynthesisKind
    priority: int  # 0-3; a recap is 2, the greeting 3
    billable_chars: int
    estimate_ms: int
    segment_offsets_ms: tuple[int, ...] = ()


@dataclass(frozen=True)
class SynthesizedAudio:
    data: bytes
    media_type: AudioMediaType
    duration_ms: int
    provider: Literal["mock", "azure"]
    request_id: str | None = None


class SpeechError(PunditError):
    """A synthesis or recognition that failed; `kind` decides retry, breaker and note."""

    kind: SpeechErrorKind = "unavailable"

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class SpeechTimeout(SpeechError):
    kind = "timeout"


class SpeechThrottled(SpeechError):
    kind = "throttled"


class SpeechAuthError(SpeechError):
    kind = "auth"


class SpeechBadRequest(SpeechError):
    kind = "bad_request"


class SpeechUnavailable(SpeechError):
    kind = "unavailable"


class BadAudio(SpeechError):
    kind = "bad_audio"


class Synthesizer(Protocol):
    @property
    def name(self) -> Literal["mock", "azure"]: ...

    @property
    def media_type(self) -> AudioMediaType: ...

    @property
    def output_format(self) -> str: ...

    async def synthesize(
        self, request: SynthesisRequest, *, timeout_s: float
    ) -> SynthesizedAudio: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class RecognitionRequest:
    wav: bytes  # PCM 16 kHz mono 16-bit, checked by the API
    language: Lang
    duration_ms: int


class Recognizer(Protocol):
    @property
    def name(self) -> Literal["mock", "azure"]: ...

    async def recognize(
        self, request: RecognitionRequest, *, timeout_s: float
    ) -> RecognitionResult: ...

    async def aclose(self) -> None: ...
