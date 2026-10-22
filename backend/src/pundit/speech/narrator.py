"""Narrator: the voice agent (docs/day5-design.md sections 2.2 and 8.7).

It turns a spoken script into a SpeechCue for one voice: the words read (numbers written out),
their prosody, SpeechGuard, and the clip of the server. `cue` never waits: a clip that does not
exist yet is made by a background task (one task per clip id, shared by every listener), through
the rate gate and the breaker, and kept in the clip cache (and the durable store for Azure clips,
recaps and the greeting). The cue carries the clip's URL at once; `GET /audio` waits for the task.
Without server audio (provider `browser`, breaker open, gate full, budget spent) the cue carries
a note instead and the browser reads the same segments. A script that fails SpeechGuard is never
read by anyone.
"""

import asyncio
import contextlib
import logging
import math
import statistics
import time
from collections import Counter, OrderedDict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pundit.agents.contracts import (
    AudioRef,
    Lang,
    RecapView,
    SpeechCue,
    SpeechNote,
    TraceSpan,
    VoiceGender,
)
from pundit.agents.narration import MomentLine, SpeechScript, greeting_script
from pundit.agents.speech_cue import CueDraft, draft_cue, draft_recap
from pundit.agents.spoken import EMPTY_LEXICON, SpokenLexicon
from pundit.agents.ssml import clip_id as make_clip_id
from pundit.agents.voices import voice_for
from pundit.llm.faults import TtsFaultPlan
from pundit.llm.settings import Settings
from pundit.orchestration.resilience import CircuitBreaker
from pundit.speech.audio import EXTENSIONS, MEDIA_TYPES, check_audio
from pundit.speech.clips import ClipCache, ClipFile, valid_clip_id
from pundit.speech.durable import DurableStore, build_durable
from pundit.speech.gate import RateGate
from pundit.speech.ports import (
    BadAudio,
    SpeechAuthError,
    SpeechError,
    SpeechThrottled,
    SynthesisKind,
    SynthesisRequest,
    Synthesizer,
)

log = logging.getLogger("pundit.speech")
AGENT = "Narrator"
FAILURE_TTL_S = 60.0
MAX_FAILURES = 1000
MAX_SPAN_ITEMS = 300
LATENCIES_KEPT = 500
AUTH_COOLDOWN_S = 300.0
RETRIES: dict[SynthesisKind, int] = {"commentary": 0, "recap": 2, "greeting": 2}
DURABLE_KINDS: frozenset[SynthesisKind] = frozenset({"recap", "greeting"})
RETRYABLE = frozenset({"timeout", "throttled", "unavailable"})


def wait_for_token(kind: SynthesisKind, priority: int) -> float:
    """How long a synthesis may wait at the gate (section 8.5)."""
    if kind == "recap":
        return 60.0
    if kind == "greeting":
        return 5.0
    return 3.0 if priority >= 2 else 1.5


def utc_day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


@dataclass(frozen=True)
class ClipFailure:
    reason: str  # "throttled" / "timeout" / "unavailable" / "auth" / "bad_audio" / ...


@dataclass
class _Job:
    request: SynthesisRequest
    item_id: str
    match_id: str | None
    metadata: dict[str, str]
    started: float  # monotonic, when the cue was made
    trace_offset_ms: float


class Narrator:
    def __init__(
        self,
        settings: Settings,
        *,
        synthesizer: Synthesizer | None,
        cache: ClipCache,
        durable: DurableStore | None = None,
        gate: RateGate | None = None,
        breaker: CircuitBreaker | None = None,
        clock: Callable[[], float] = time.monotonic,
        day: Callable[[], str] = utc_day,
    ) -> None:
        self.settings = settings
        self.synthesizer = synthesizer
        self.cache = cache
        self.durable = durable
        self.gate = gate or RateGate(settings.rate_per_minute, settings.tts_max_concurrency)
        self.breaker = breaker or CircuitBreaker()
        self._clock = clock
        self._day = day
        self._inflight: dict[str, asyncio.Task[None]] = {}
        self._failures: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._spans: OrderedDict[str, list[TraceSpan]] = OrderedDict()
        self._chars = (day(), 0)
        self._latencies: deque[float] = deque(maxlen=LATENCIES_KEPT)
        self.counts: Counter[str] = Counter()
        self.auth_error = False

    # ------------------------------------------------------------------ configuration

    @property
    def provider(self) -> str:
        return self.settings.tts_provider

    @property
    def server_audio(self) -> bool:
        return self.synthesizer is not None and self.provider in ("mock", "azure")

    def speaks(self, language: Lang) -> bool:
        return self.provider != "off" and language in self.settings.tts_languages

    @property
    def extension(self) -> str:
        assert self.synthesizer is not None
        return EXTENSIONS[self.synthesizer.media_type]

    # ------------------------------------------------------------------ cues (never wait)

    def cue(
        self,
        script: SpeechScript,
        lexicon: SpokenLexicon,
        *,
        gender: VoiceGender,
        item_id: str,
        priority: int,
        session_speed: float = 1.0,
        match_id: str | None = None,
        trace_offset_ms: float = 0.0,
    ) -> SpeechCue | None:
        if not self.speaks(script.language) or not script.parts:
            return None
        started = self._clock()
        draft = draft_cue(script, voice_for(script.language, gender), lexicon)
        return self._finish(
            draft, script.kind, gender=gender, item_id=item_id, priority=priority,
            session_speed=session_speed, match_id=match_id, started=started,
            trace_offset_ms=trace_offset_ms, scope=None,
        )  # fmt: skip

    def recap_cue(
        self,
        view: RecapView,
        lines: Sequence[MomentLine],
        as_of_seq: int,
        lexicon: SpokenLexicon,
        *,
        gender: VoiceGender,
        match_id: str | None = None,
    ) -> SpeechCue | None:
        if not self.speaks(view.language):
            return None
        started = self._clock()
        draft = draft_recap(view, lines, as_of_seq, voice_for(view.language, gender), lexicon)
        return self._finish(
            draft, "recap", gender=gender, item_id=view.id, priority=2, session_speed=1.0,
            match_id=match_id, started=started, trace_offset_ms=0.0, scope=view.scope,
        )  # fmt: skip

    def greeting_cue(self, language: Lang, gender: VoiceGender) -> SpeechCue | None:
        if not self.speaks(language):
            return None
        started = self._clock()
        draft = draft_cue(greeting_script(language), voice_for(language, gender), EMPTY_LEXICON)
        return self._finish(
            draft, "greeting", gender=gender, item_id=f"greeting-{language}-{gender}",
            priority=3, session_speed=1.0, match_id=None, started=started,
            trace_offset_ms=0.0, scope=None,
        )  # fmt: skip

    def _finish(
        self,
        draft: CueDraft,
        kind: SynthesisKind,
        *,
        gender: VoiceGender,
        item_id: str,
        priority: int,
        session_speed: float,
        match_id: str | None,
        started: float,
        trace_offset_ms: float,
        scope: str | None,
    ) -> SpeechCue:
        voice = draft.voice
        narrate_ms = (self._clock() - started) * 1000
        summary = {
            "kind": kind, "voice": voice.name, "preset": draft.preset,
            "segments": len(draft.segments), "chars": sum(len(s.text) for s in draft.segments),
            "estimate_ms": draft.estimate_ms, "ssml": draft.ssml[:300],
        }  # fmt: skip
        self._span(item_id, "narrate", "agent", trace_offset_ms, narrate_ms, summary, {})
        at_ms = trace_offset_ms + narrate_ms

        def make(audio: AudioRef | None = None, note: SpeechNote | None = None) -> SpeechCue:
            return SpeechCue(
                lang=voice.lang, voice=voice.name, gender=gender, preset=draft.preset,
                segments=() if note == "guard" else draft.segments, audio=audio, note=note,
            )  # fmt: skip

        def without_audio(note: SpeechNote) -> SpeechCue:
            self.counts[f"note_{note}"] += 1
            self._span(item_id, "fallback", "fallback", at_ms, 0.0, {}, {"note": note},
                       status="fallback")  # fmt: skip
            return make(note=note)

        if not draft.report.passed:
            self.counts["guard_rejected"] += 1
            problems = [f"{p.code}: {p.detail}" for p in draft.report.problems][:5]
            log.warning("speech guard rejected %s: %s", item_id, problems)
            self._span(item_id, "speech_guard", "guardrail", at_ms, 0.0, {},
                       {"codes": draft.report.codes(), "problems": problems},
                       status="rejected")  # fmt: skip
            return make(note="guard")
        if not self.server_audio:
            return without_audio("server_off")
        if kind == "commentary" and session_speed > self.settings.tts_max_speed:
            return without_audio("speed")
        assert self.synthesizer is not None
        clip_id = make_clip_id(
            self.synthesizer.name, voice.name, self.synthesizer.output_format, draft.ssml
        )
        audio = self._audio_ref(clip_id, draft.estimate_ms)
        if audio.exact or clip_id in self._inflight:  # made, or being made for someone else
            self.counts["cache_hits" if audio.exact else "joined"] += 1
            return make(audio=audio)
        if self._over_budget(draft.billable_chars):
            return without_audio("budget")
        if not self.breaker.allow():  # last: in half-open it lets exactly one call through
            return without_audio("circuit_open")
        request = SynthesisRequest(
            clip_id=clip_id, ssml=draft.ssml, voice=voice.name, lang=voice.lang, kind=kind,
            priority=priority, billable_chars=draft.billable_chars,
            estimate_ms=draft.estimate_ms, segment_offsets_ms=draft.segment_offsets_ms,
        )  # fmt: skip
        metadata = {
            "kind": kind, "voice": voice.name, "preset": draft.preset,
            "chars": str(draft.billable_chars), "provider": self.synthesizer.name,
            "match_id": match_id or "-", "scope": scope or "-", "item_id": item_id,
        }  # fmt: skip
        job = _Job(request, item_id, match_id, metadata, started, at_ms)
        self._inflight[clip_id] = asyncio.get_running_loop().create_task(
            self._produce(job), name=f"tts:{clip_id[:8]}"
        )
        return make(audio=audio)

    def _over_budget(self, chars: int) -> bool:
        """Would this synthesis pass the characters the app may bill today (UTC)?"""
        day, used = self._chars
        if day != self._day():
            self._chars = (self._day(), 0)
            used = 0
        return used + chars > self.settings.tts_daily_char_budget

    def _audio_ref(self, clip_id: str, estimate_ms: int) -> AudioRef:
        assert self.synthesizer is not None
        media_type = self.synthesizer.media_type
        url = f"/audio/{clip_id}.{EXTENSIONS[media_type]}"
        cached = self.cache.get(clip_id)
        if cached is not None:
            return AudioRef(clip_id=clip_id, url=url, media_type=media_type,
                            duration_ms=cached.duration_ms, exact=True)  # fmt: skip
        return AudioRef(clip_id=clip_id, url=url, media_type=media_type,
                        duration_ms=estimate_ms, exact=False)  # fmt: skip

    # ------------------------------------------------------------------ production (background)

    async def _produce(self, job: _Job) -> None:
        request = job.request
        began = self._clock()
        status: Literal["ok", "error"] = "ok"
        cache_kind, error, duration_ms, size = "miss", None, 0, 0
        try:
            stored = await self._from_durable(request.clip_id, self.extension)
            if stored is not None:
                cache_kind, duration_ms, size = "durable", stored.duration_ms, stored.size
                self.counts["durable_hits"] += 1
            else:
                duration_ms, size = await self._synthesize(job)
        except SpeechError as exc:
            status, error = "error", f"{exc.kind}: {exc}"
            self._remember(request.clip_id, exc.kind)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one broken clip must not stop the voice of the match
            log.exception("speech job %s failed", request.clip_id)
            status, error = "error", f"error: {type(exc).__name__}"
            self._remember(request.clip_id, "error")
            self.counts["error_unexpected"] += 1
            self.breaker.record_failure()
        finally:
            self._inflight.pop(request.clip_id, None)
        elapsed = (self._clock() - began) * 1000
        start_ms = job.trace_offset_ms + (began - job.started) * 1000
        output = {"clip_id": request.clip_id, "cache": cache_kind, "provider": self.provider,
                  "bytes": size, "duration_ms": duration_ms}  # fmt: skip
        self._span(job.item_id, "tts.synthesize", "tool", start_ms, elapsed,
                   {"voice": request.voice, "kind": request.kind, "chars": request.billable_chars},
                   output, status=status, error=error)  # fmt: skip
        log.info(
            "speech.synthesize clip_id=%s voice=%s kind=%s chars=%d ms=%.0f status=%s cache=%s "
            "provider=%s match_id=%s item_id=%s",
            request.clip_id, request.voice, request.kind, request.billable_chars, elapsed,
            status, cache_kind, self.provider, job.match_id or "-", job.item_id,
        )  # fmt: skip

    async def _from_durable(self, clip_id: str, extension: str) -> ClipFile | None:
        if self.durable is None:
            return None
        try:
            data = await self.durable.get(clip_id, extension)
        except Exception:  # an unreachable store only costs a synthesis
            log.warning("durable store read failed for %s", clip_id, exc_info=True)
            return None
        if data is None:
            return None
        media_type = MEDIA_TYPES[extension]
        try:
            duration = check_audio(data, media_type)
        except ValueError:
            log.warning("durable clip %s is not valid audio", clip_id)
            return None
        return self.cache.put(clip_id, data, media_type, duration)

    async def _synthesize(self, job: _Job) -> tuple[int, int]:
        request = job.request
        assert self.synthesizer is not None
        attempts = 1 + RETRIES[request.kind]
        timeout = (
            self.settings.tts_recap_timeout_s
            if request.kind == "recap"
            else self.settings.tts_timeout_s
        )
        for attempt in range(1, attempts + 1):
            if not await self.gate.acquire(
                request.priority, wait_for_token(request.kind, request.priority)
            ):
                self.counts["throttled"] += 1
                raise SpeechThrottled("no slot at the rate gate")  # not a failure of Azure
            began = self._clock()
            try:
                audio = await self.synthesizer.synthesize(request, timeout_s=timeout)
                duration = check_audio(audio.data, audio.media_type)
            except ValueError as exc:  # HTTP 200 that is not audio: a fault of the service
                self.breaker.record_failure()
                self.counts["error_bad_audio"] += 1
                raise BadAudio(str(exc)) from None
            except SpeechError as exc:
                self.counts[f"error_{exc.kind}"] += 1
                if isinstance(exc, SpeechAuthError):
                    self.auth_error = True
                    self.breaker.trip(AUTH_COOLDOWN_S)
                    raise
                self.breaker.record_failure()
                if isinstance(exc, SpeechThrottled):
                    self.gate.cooldown(exc.retry_after_s or 5.0)
                if attempt == attempts or exc.kind not in RETRYABLE:
                    raise
                continue
            finally:
                await self.gate.release()
            self.breaker.record_success()
            self.auth_error = False
            self._latencies.append((self._clock() - began) * 1000)
            self.counts["synthesized"] += 1
            day, used = self._chars
            self._chars = (day, used + request.billable_chars)
            clip = self.cache.put(request.clip_id, audio.data, audio.media_type, duration)
            if self.durable is not None and (
                audio.provider == "azure" or request.kind in DURABLE_KINDS
            ):
                try:
                    await self.durable.put(
                        request.clip_id, clip.extension, audio.data, audio.media_type,
                        {**job.metadata, "duration_ms": str(duration)},
                    )  # fmt: skip
                except Exception:  # the clip is served from the cache anyway
                    log.warning("durable store write failed for %s", request.clip_id,
                                exc_info=True)  # fmt: skip
            return duration, len(audio.data)
        raise AssertionError("unreachable")

    def _remember(self, clip_id: str, reason: str) -> None:
        self._failures[clip_id] = (reason, self._clock())
        self._failures.move_to_end(clip_id)
        while len(self._failures) > MAX_FAILURES:
            self._failures.popitem(last=False)

    def _failure(self, clip_id: str) -> str | None:
        found = self._failures.get(clip_id)
        if found is None:
            return None
        reason, at = found
        if self._clock() - at > FAILURE_TTL_S:
            del self._failures[clip_id]
            return None
        return reason

    # ------------------------------------------------------------------ serving (GET /audio)

    async def wait(
        self, clip_id: str, extension: str, timeout_s: float
    ) -> ClipFile | ClipFailure | None:
        """The clip, made or being made (waiting up to `timeout_s`); a failure; or None."""
        if not valid_clip_id(clip_id) or extension not in MEDIA_TYPES:
            return None
        found = self.cache.get(clip_id)
        if found is not None:
            return found
        task = self._inflight.get(clip_id)
        if task is not None:
            # asyncio.wait neither cancels the task nor raises its outcome: a listener who
            # leaves (or times out) never stops a clip that others will fetch
            done, _ = await asyncio.wait({task}, timeout=timeout_s)
            if not done:
                return ClipFailure("timeout")
            found = self.cache.get(clip_id)
            if found is not None:
                return found
        reason = self._failure(clip_id)
        if reason is not None:
            return ClipFailure(reason)
        return await self._from_durable(clip_id, extension)

    # ------------------------------------------------------------------ trace and numbers

    def _span(
        self,
        item_id: str,
        name: str,
        kind: Literal["agent", "tool", "guardrail", "fallback"],
        start_ms: float,
        duration_ms: float,
        input: dict[str, Any],
        output: dict[str, Any],
        *,
        status: Literal["ok", "error", "fallback", "rejected"] = "ok",
        error: str | None = None,
    ) -> None:
        spans = self._spans.setdefault(item_id, [])
        self._spans.move_to_end(item_id)
        while len(self._spans) > MAX_SPAN_ITEMS:
            self._spans.popitem(last=False)
        spans.append(
            TraceSpan(
                trace_id=item_id,
                span_id=f"{item_id}-v{len(spans) + 1:02d}",
                parent_id=None,
                agent=AGENT,
                kind=kind,
                name=name,
                input=input,
                output=output,
                status=status,
                attempt=1,
                # rounded up: a span never starts before the studio job it follows
                start_ms=math.ceil(start_ms * 1000) / 1000,
                duration_ms=round(duration_ms, 3),
                model=self.provider,
                error=error,
            )
        )

    def spans_of(self, item_id: str) -> tuple[TraceSpan, ...]:
        return tuple(self._spans.get(item_id, ()))

    def latency_p95(self) -> float | None:
        values = list(self._latencies)
        if not values:
            return None
        if len(values) == 1:
            return round(values[0], 1)
        return round(statistics.quantiles(values, n=20)[18], 1)

    def stats(self) -> dict[str, Any]:
        day, used = self._chars
        errors = {
            k.removeprefix("error_"): v for k, v in self.counts.items() if k.startswith("error_")
        }
        return {
            "provider": self.provider,
            "languages": list(self.settings.tts_languages),
            "store": self.durable.name if self.durable is not None else "none",
            "breaker": self.breaker.state,
            "degraded": ["auth_error"] if self.auth_error else [],
            "synthesized": self.counts["synthesized"],
            "cache_hits": self.counts["cache_hits"],
            "durable_hits": self.counts["durable_hits"],
            "throttled": self.counts["throttled"],
            "guard_rejected": self.counts["guard_rejected"],
            "errors": dict(sorted(errors.items())),
            "chars_today": used if day == self._day() else 0,
            "char_budget": self.settings.tts_daily_char_budget,
            "latency_ms_p95": self.latency_p95(),
            "queue": len(self._inflight),
            "cache": self.cache.stats(),
            "gate": self.gate.stats(),
        }

    def active(self) -> bool:
        return any(not task.done() for task in self._inflight.values())

    async def idle(self) -> None:
        """Until every clip being made is done (tests and the CLI)."""
        while self._inflight:
            await asyncio.gather(*list(self._inflight.values()), return_exceptions=True)

    async def aclose(self) -> None:
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._inflight.clear()
        if self.synthesizer is not None:
            await self.synthesizer.aclose()
        if self.durable is not None:
            await self.durable.aclose()


def build_synthesizer(settings: Settings) -> Synthesizer | None:
    if settings.tts_provider == "mock":
        from pundit.speech.mock import MockSynthesizer

        return MockSynthesizer(
            time_scale=settings.mock_tts_time_scale,
            latency_ms=settings.mock_tts_latency_ms,
            faults=TtsFaultPlan.parse(settings.mock_tts_faults),
        )
    if settings.tts_provider == "azure":
        from pundit.speech.auth import build_auth
        from pundit.speech.azure_tts import AzureSynthesizer, tts_url

        auth = build_auth(settings)
        if auth is None:
            raise ValueError("TTS_PROVIDER=azure needs a key or Entra ID settings")
        return AzureSynthesizer(tts_url(settings), auth)
    return None


def build_narrator(settings: Settings) -> Narrator | None:
    """The app's Narrator, or None when the voice is off (TTS_PROVIDER=off)."""
    if not settings.speech_enabled:
        return None
    return Narrator(
        settings,
        synthesizer=build_synthesizer(settings),
        cache=ClipCache(settings.cache_dir, settings.audio_cache_max_mb * 1024 * 1024),
        durable=build_durable(settings),
    )
