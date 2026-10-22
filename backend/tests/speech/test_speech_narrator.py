"""The Narrator (docs/day5-design.md section 8.7): cues that never wait, one clip per content,
the notes when there is no server audio, the breaker, retries, the durable store, shutdown."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from helpers import match_context, run

from pundit.agents.contracts import CommentaryView, SpeechCue
from pundit.agents.narration import ScriptPart, SpeechScript, commentary_script
from pundit.agents.spoken import SpokenLexicon
from pundit.llm.settings import Settings
from pundit.speech.clips import ClipFile
from pundit.speech.mock import MockSynthesizer
from pundit.speech.narrator import ClipFailure, Narrator, build_narrator

LEX = SpokenLexicon.from_match(match_context("dominant_loser", 42))


def narrator_for(tmp_path: Path, **values: Any) -> Narrator:
    settings: dict[str, Any] = {
        "tts_provider": "mock", "audio_cache_dir": str(tmp_path / "cache"),
        "audio_store": "local", "audio_store_dir": str(tmp_path / "store"),
        "mock_tts_time_scale": 0.01, "tts_timeout_s": 0.5, "tts_recap_timeout_s": 0.5,
    }  # fmt: skip
    settings.update(values)
    narrator = build_narrator(Settings(**settings))
    assert narrator is not None
    return narrator


def line(minute: int = 27, **values: Any) -> SpeechScript:
    base: dict[str, Any] = {
        "id": f"x-m{minute:04d}-c", "trigger_id": f"x-m{minute:04d}", "kind": "goal", "period": 1,
        "clock_ms": minute * 60_000, "display_minute": f"{minute}'", "importance": 3,
        "language": "vi", "mode": "analyst", "excitement": 3, "speak": True,
        "text": f"Phút {minute}, M. Ribas ghi bàn. Harbor Lions FC 0-1 Kestrel Athletic.",
        "source": "template",
    }  # fmt: skip
    base.update(values)
    script = commentary_script(CommentaryView(**base))
    assert script is not None
    return script


def cue(narrator: Narrator, script: SpeechScript, **values: Any) -> SpeechCue:
    options: dict[str, Any] = {"gender": "female", "item_id": "x-m0027-c", "priority": 3}
    options.update(values)
    made = narrator.cue(script, LEX, **options)
    assert made is not None
    return made


def calls(narrator: Narrator) -> int:
    assert isinstance(narrator.synthesizer, MockSynthesizer)
    return narrator.synthesizer.calls


def test_one_clip_per_content_shared_by_every_listener(tmp_path: Path) -> None:
    async def scenario() -> None:
        narrator = narrator_for(tmp_path)
        cues = [cue(narrator, line(), item_id=f"i{k}") for k in range(10)]
        assert len({c.audio.clip_id for c in cues if c.audio}) == 1
        first = cues[0].audio
        assert first is not None and not first.exact and first.url.endswith(".wav")
        await narrator.idle()
        again = cue(narrator, line())
        assert again.audio is not None and again.audio.exact and again.audio.duration_ms > 0
        clip = await narrator.wait(first.clip_id, "wav", 1)
        assert isinstance(clip, ClipFile) and clip.media_type == "audio/wav"
        assert calls(narrator) == 1
        stats = narrator.stats()
        assert (stats["synthesized"], stats["cache_hits"]) == (1, 1)
        other = cue(narrator, line(), gender="male")
        assert other.audio is not None and other.audio.clip_id != first.clip_id
        assert other.voice == "vi-VN-NamMinhNeural"
        await narrator.aclose()

    run(scenario())


def test_cue_segments_follow_the_script(tmp_path: Path) -> None:
    async def scenario() -> None:
        narrator = narrator_for(tmp_path)
        made = cue(narrator, line(text="VÀOOO! J. Ellery ghi bàn! Harbor Lions FC 1-2 KES."))
        assert [s.preset for s in made.segments] == ["burst", "peak"]
        assert made.segments[0].text == "Vào!" and made.segments[1].pause_before_ms == 250
        assert made.preset == "peak" and made.lang == "vi-VN" and made.gender == "female"
        await narrator.aclose()

    run(scenario())


def test_guard_rejection_is_never_read(tmp_path: Path) -> None:
    async def scenario() -> None:
        narrator = narrator_for(tmp_path)
        bad = SpeechScript("commentary", "vi", (ScriptPart("phrase", "Xin chào.", "calm"),))
        made = cue(narrator, bad, item_id="bad-c")
        assert made.note == "guard" and made.audio is None and made.segments == ()
        assert narrator.stats()["guard_rejected"] == 1 and calls(narrator) == 0
        assert [s.status for s in narrator.spans_of("bad-c")] == ["ok", "rejected"]
        await narrator.aclose()

    run(scenario())


def test_browser_provider_sends_the_script_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        narrator = narrator_for(tmp_path, tts_provider="browser")
        made = cue(narrator, line())
        assert made.note == "server_off" and made.audio is None and made.segments
        assert narrator.synthesizer is None and not narrator.server_audio
        assert not (tmp_path / "cache").exists()
        await narrator.aclose()

    run(scenario())


def test_speed_budget_and_language(tmp_path: Path) -> None:
    async def scenario() -> None:
        narrator = narrator_for(tmp_path, tts_daily_char_budget=10)
        assert cue(narrator, line(), session_speed=1000).note == "speed"
        assert cue(narrator, line()).note == "budget"
        en = SpeechScript("commentary", "en", (ScriptPart("commentary", "Kick-off.", "calm"),))
        assert narrator.cue(en, LEX, gender="female", item_id="e", priority=0) is None
        assert narrator.stats()["synthesized"] == 0
        await narrator.aclose()

    run(scenario())


def test_breaker_opens_after_three_failures_and_failures_are_remembered(tmp_path: Path) -> None:
    async def scenario() -> None:
        narrator = narrator_for(tmp_path, mock_tts_faults="error")
        failed = [cue(narrator, line(minute)) for minute in (10, 20, 30)]
        await narrator.idle()
        assert narrator.breaker.state == "open"
        assert cue(narrator, line(40)).note == "circuit_open"
        first = failed[0].audio
        assert first is not None
        found = await narrator.wait(first.clip_id, "wav", 1)
        assert found == ClipFailure("unavailable")
        stats = narrator.stats()
        assert stats["breaker"] == "open" and stats["errors"] == {"unavailable": 3}
        await narrator.aclose()

    run(scenario())


def test_auth_error_trips_the_breaker(tmp_path: Path) -> None:
    async def scenario() -> None:
        narrator = narrator_for(tmp_path, mock_tts_faults="auth:1")
        made = cue(narrator, line())
        await narrator.idle()
        assert narrator.breaker.state == "open" and narrator.stats()["degraded"] == ["auth_error"]
        assert cue(narrator, line(50)).note == "circuit_open"
        assert made.audio is not None
        assert await narrator.wait(made.audio.clip_id, "wav", 1) == ClipFailure("auth")
        await narrator.aclose()

    run(scenario())


def test_greeting_retries_after_a_429(tmp_path: Path) -> None:
    async def scenario() -> None:
        narrator = narrator_for(tmp_path, mock_tts_faults="throttle:1")
        made = narrator.greeting_cue("vi", "male")
        assert made is not None and made.audio is not None and made.preset == "greeting"
        clip = await narrator.wait(made.audio.clip_id, "wav", 5)  # cooldown 1 s, then a retry
        assert isinstance(clip, ClipFile) and calls(narrator) == 2
        await narrator.aclose()

    run(scenario())


def test_bad_audio_and_timeouts_are_failures(tmp_path: Path) -> None:
    async def scenario() -> None:
        narrator = narrator_for(tmp_path, mock_tts_faults="bad_audio:1")
        made = cue(narrator, line())
        assert made.audio is not None
        assert await narrator.wait(made.audio.clip_id, "wav", 1) == ClipFailure("bad_audio")
        await narrator.aclose()
        slow = narrator_for(tmp_path / "slow", mock_tts_latency_ms=300, tts_timeout_s=0.05)
        late = cue(slow, line())
        assert late.audio is not None
        assert await slow.wait(late.audio.clip_id, "wav", 2) == ClipFailure("timeout")
        await slow.aclose()

    run(scenario())


def test_a_listener_who_waits_too_long_gets_a_timeout_not_a_cancel(tmp_path: Path) -> None:
    async def scenario() -> None:
        narrator = narrator_for(tmp_path, mock_tts_latency_ms=300, tts_timeout_s=2)
        made = cue(narrator, line())
        assert made.audio is not None
        assert await narrator.wait(made.audio.clip_id, "wav", 0.01) == ClipFailure("timeout")
        clip = await narrator.wait(made.audio.clip_id, "wav", 2)  # the clip was not stopped
        assert isinstance(clip, ClipFile)
        await narrator.aclose()

    run(scenario())


def test_gate_throttles_live_lines(tmp_path: Path) -> None:
    async def scenario() -> None:
        narrator = narrator_for(tmp_path, tts_max_rpm=1)
        first = cue(narrator, line(10), priority=0)
        second = cue(narrator, line(20), priority=0)  # no token for 60 s, waits 1.5 s at most
        await narrator.idle()
        assert first.audio is not None and second.audio is not None
        assert isinstance(await narrator.wait(first.audio.clip_id, "wav", 1), ClipFile)
        assert await narrator.wait(second.audio.clip_id, "wav", 1) == ClipFailure("throttled")
        assert narrator.breaker.state == "closed"  # the gate is not a failure of the service
        await narrator.aclose()

    run(scenario())


def test_durable_store_keeps_greetings_and_recaps_not_mock_lines(tmp_path: Path) -> None:
    async def scenario() -> None:
        narrator = narrator_for(tmp_path)
        live = cue(narrator, line())
        greeting = narrator.greeting_cue("vi", "female")
        await narrator.idle()
        assert greeting is not None and greeting.audio is not None and live.audio is not None
        stored = {p.name for p in (tmp_path / "store").iterdir()}
        assert f"{greeting.audio.clip_id}.wav" in stored
        assert f"{live.audio.clip_id}.wav" not in stored
        await narrator.aclose()
        restarted = narrator_for(tmp_path, audio_cache_dir=str(tmp_path / "cache2"))
        clip = await restarted.wait(greeting.audio.clip_id, "wav", 1)  # an old link still plays
        assert isinstance(clip, ClipFile) and calls(restarted) == 0
        again = restarted.greeting_cue("vi", "female")
        assert again is not None and again.audio is not None and again.audio.exact
        assert await restarted.wait(live.audio.clip_id, "wav", 1) is None  # gone: 404
        await restarted.aclose()

    run(scenario())


def test_spans_shutdown_and_unknown_clips(tmp_path: Path) -> None:
    async def scenario() -> None:
        narrator = narrator_for(tmp_path)
        cue(narrator, line(), item_id="x-m0027-c", trace_offset_ms=120.0)
        await narrator.idle()
        spans = narrator.spans_of("x-m0027-c")
        assert [(s.agent, s.kind, s.name) for s in spans] == [
            ("Narrator", "agent", "narrate"), ("Narrator", "tool", "tts.synthesize"),
        ]  # fmt: skip
        assert spans[0].span_id == "x-m0027-c-v01" and spans[0].start_ms == 120.0
        assert spans[1].output["cache"] == "miss" and spans[1].start_ms >= 120.0
        assert await narrator.wait("f" * 32, "wav", 0.1) is None
        assert await narrator.wait("../x", "wav", 0.1) is None
        await narrator.aclose()
        slow = narrator_for(tmp_path / "slow", mock_tts_latency_ms=5000, tts_timeout_s=10)
        cue(slow, line())
        assert slow.active()
        await slow.aclose()
        assert not slow.active()
        await asyncio.sleep(0)

    run(scenario())


@pytest.mark.parametrize("offset", [45.29020003974438, 12.3451, 7.0, 0.0004])
def test_a_span_never_starts_before_the_job_it_follows(tmp_path: Path, offset: float) -> None:
    """Rounding the start down would put a Narrator span before the studio's end on the
    Gantt (found by the voice API test: 45.2902 rounded to 45.290)."""
    narrator = narrator_for(tmp_path)
    narrator._span("c-1", "narrate", "agent", offset, 1.23456, {}, {})
    (span,) = narrator.spans_of("c-1")
    assert offset <= span.start_ms <= offset + 0.001
    assert span.duration_ms == 1.235
