"""Day 5 time budgets (docs/day5-design.md section 13, V8). Skipped under coverage.

The voice is drafted on the replay loop (normalization, SpeechGuard, SSML, the clip's name) for
every listening viewer and synthesized in the background: with three listeners and every clip
made, a replay at 1000x is at most 20 % slower than without the voice. A clip is ready soon
after its line reaches the viewer (mock voice 300 ms): the event -> line part is the studio's
lag (Day 3, under 300 ms), the line -> clip part is measured here."""

import asyncio
import statistics
import sys
import time
from pathlib import Path

import pytest
from helpers import generated, mock_service, run

from pundit.agents.contracts import Profile, WsMessage
from pundit.api.app import default_crew
from pundit.api.hub import Client
from pundit.api.sessions import MatchSession
from pundit.engine.replay import RealClock
from pundit.llm.settings import Settings
from pundit.speech.clips import ClipCache
from pundit.speech.mock import MockSynthesizer
from pundit.speech.narrator import Narrator
from pundit.speech.ports import SynthesisRequest, SynthesizedAudio

pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif("coverage" in sys.modules, reason="timings are meaningless under coverage"),
]

LISTENERS = (
    Profile(mode="casual", language="vi", team="harbor_lions", beginner=True, voice="female"),
    Profile(mode="analyst", language="vi", voice="male"),
    Profile(mode="casual", language="vi", voice="female"),
)


def p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=20, method="inclusive")[-1]


class TimedMock(MockSynthesizer):
    """The mock voice, noting when each clip is ready."""

    def __init__(self, *, time_scale: float, latency_ms: float) -> None:
        super().__init__(time_scale=time_scale, latency_ms=latency_ms)
        self.ready: dict[str, float] = {}

    async def synthesize(self, request: SynthesisRequest, *, timeout_s: float) -> SynthesizedAudio:
        audio = await super().synthesize(request, timeout_s=timeout_s)
        self.ready[request.clip_id] = time.perf_counter()
        return audio


def _play(
    root: Path, *, voice: bool, speed: float, latency_ms: float = 0.0
) -> tuple[float, dict[str, float], TimedMock | None, Narrator | None]:
    settings = Settings(
        tts_provider="mock" if voice else "off",
        tts_max_speed=1_000_000.0,  # read at any speed: the worst case for the replay
        mock_tts_latency_ms=latency_ms,
        audio_cache_dir=str(root / "cache"),
        audio_store_dir=str(root / "store"),
    )
    synthesizer = TimedMock(time_scale=0.01, latency_ms=latency_ms) if voice else None
    narrator = (
        Narrator(settings, synthesizer=synthesizer, cache=ClipCache(settings.cache_dir, 64 << 20))
        if synthesizer is not None
        else None
    )
    session = MatchSession(
        generated("dominant_loser", 42),
        service=mock_service(),
        speed=speed,
        clock=RealClock(),
        studio=default_crew(settings),
        narrator=narrator,
    )
    first_seen: dict[str, float] = {}

    async def drain(client: Client) -> None:
        while True:
            message: WsMessage = await client.queue.get()
            speech = message.payload.get("speech") if message.type == "commentary" else None
            if speech is not None and speech.get("audio") is not None:
                first_seen.setdefault(speech["audio"]["clip_id"], time.perf_counter())

    async def play() -> float:
        clients = [session.hub.subscribe(1_000_000, profile) for profile in LISTENERS]
        drains = [asyncio.create_task(drain(client)) for client in clients]
        started = time.perf_counter()
        session.start()
        await session.wait()
        elapsed = time.perf_counter() - started
        if session.director is not None:
            await session.director.idle()
        if narrator is not None:
            await narrator.idle()
        await asyncio.sleep(0)
        for task in drains:
            task.cancel()
        if session.director is not None:
            await session.director.stop()
        if narrator is not None:
            await narrator.aclose()
        return elapsed

    return run(play()), first_seen, synthesizer, narrator


def test_three_listeners_slow_a_1000x_replay_by_at_most_20_percent(tmp_path: Path) -> None:
    without, _, _, _ = _play(tmp_path / "off", voice=False, speed=1000.0)
    with_voice, seen, synthesizer, narrator = _play(tmp_path / "on", voice=True, speed=1000.0)
    assert synthesizer is not None and narrator is not None
    assert with_voice <= without * 1.2, (without, with_voice)
    assert len(seen) > 30 and set(seen) <= set(synthesizer.ready)  # every clip was made
    assert narrator.counts["synthesized"] == len(synthesizer.ready)  # once each
    assert narrator.cache.stats()["clips"] == len(synthesizer.ready)


def test_a_clip_is_ready_soon_after_its_line_at_200x(tmp_path: Path) -> None:
    """Line -> clip ready p95 under 1.2 s with a 300 ms voice (event -> clip under 1.5 s)."""
    _, seen, synthesizer, _ = _play(tmp_path, voice=True, speed=200.0, latency_ms=300.0)
    assert synthesizer is not None
    waits = [(synthesizer.ready[clip] - at) * 1000 for clip, at in seen.items()]
    assert len(waits) > 30
    assert min(waits) >= 250  # the voice really took its 300 ms
    assert p95(waits) < 1200, p95(waits)
