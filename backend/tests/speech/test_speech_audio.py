"""Audio bytes and the mock voice (docs/day5-design.md sections 8.2 and 11): deterministic WAV,
durations, checks of what a synthesizer returns, injected faults."""

import asyncio
import struct

import pytest
from helpers import run

from pundit.llm.faults import TtsFaultPlan
from pundit.speech.audio import (
    check_audio,
    is_mp3,
    mock_wav,
    mp3_duration_ms,
    mp3_duration_of_size,
    parse_wav,
    wav_duration_of_size,
    wav_header,
)
from pundit.speech.mock import MockRecognizer, MockSynthesizer
from pundit.speech.ports import (
    RecognitionRequest,
    SpeechAuthError,
    SpeechThrottled,
    SpeechTimeout,
    SpeechUnavailable,
    SynthesisRequest,
)

REQUEST = SynthesisRequest(
    clip_id="0" * 32, ssml="<speak/>", voice="vi-VN-HoaiMyNeural", lang="vi-VN",
    kind="commentary", priority=1, billable_chars=10, estimate_ms=2000,
    segment_offsets_ms=(0, 900),
)  # fmt: skip


def fake_mp3(seconds: float, id3: bool = True) -> bytes:
    body = b"\xff\xfb\x90\x00" + bytes(round(6000 * seconds) - 4)  # 48 kbit/s = 6000 bytes/s
    header = b"ID3\x04\x00\x00\x00\x00\x00\x0a" + bytes(10) if id3 else b""
    return header + body


def test_mock_wav_is_valid_deterministic_and_toned() -> None:
    data = mock_wav(1500, (0, 1000))
    assert data == mock_wav(1500, (0, 1000))
    info = parse_wav(data)
    assert (info.channels, info.sample_rate, info.bits) == (1, 16_000, 16)
    assert info.duration_ms == 1500 and info.data_offset == 44
    samples = struct.unpack(f"<{info.data_bytes // 2}h", data[44:])
    assert any(samples[16_000 * 1 + i] for i in range(200))  # a tone where segment 2 starts
    assert not any(samples[8000:9000])  # silence in between
    assert check_audio(data, "audio/wav") == 1500
    assert wav_duration_of_size(len(data), data[:4096]) == 1500


def test_mp3_checks_and_durations() -> None:
    mp3 = fake_mp3(2.0)
    assert is_mp3(mp3) and is_mp3(fake_mp3(1.0, id3=False))
    assert mp3_duration_ms(mp3) == 2000
    assert mp3_duration_of_size(len(mp3), mp3[:10]) == 2000
    assert check_audio(mp3, "audio/mpeg") == 2000
    for bad, media in (
        (b"RIFF....", "audio/mpeg"),
        (b"x" * 5000, "audio/mpeg"),
        (b"", "audio/wav"),
    ):
        with pytest.raises(ValueError):
            check_audio(bad, media)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        check_audio(wav_header(0), "audio/wav")  # an empty WAV


def test_mock_synthesizer_follows_the_estimate() -> None:
    synth = MockSynthesizer(time_scale=0.5)
    audio = run(synth.synthesize(REQUEST, timeout_s=1))
    assert audio.media_type == "audio/wav" and audio.duration_ms == 1000 and synth.calls == 1
    assert check_audio(audio.data, "audio/wav") == 1000
    assert (synth.name, synth.media_type, synth.output_format) == (
        "mock", "audio/wav", "riff-16khz-16bit-mono-pcm",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("fault", "error"),
    [("throttle", SpeechThrottled), ("auth", SpeechAuthError), ("error", SpeechUnavailable)],
)
def test_mock_faults(fault: str, error: type[Exception]) -> None:
    synth = MockSynthesizer(faults=TtsFaultPlan.parse(f"{fault}:1"))
    with pytest.raises(error):
        run(synth.synthesize(REQUEST, timeout_s=1))
    assert run(synth.synthesize(REQUEST, timeout_s=1)).duration_ms == 2000  # only once


def test_mock_timeout_bad_audio_and_forever() -> None:
    synth = MockSynthesizer(faults=TtsFaultPlan.parse("timeout:1,bad_audio:1,error"))
    with pytest.raises(SpeechTimeout):
        run(synth.synthesize(REQUEST, timeout_s=0.01))
    audio = run(synth.synthesize(REQUEST, timeout_s=1))
    with pytest.raises(ValueError):
        check_audio(audio.data, audio.media_type)
    for _ in range(3):
        with pytest.raises(SpeechUnavailable):
            run(synth.synthesize(REQUEST, timeout_s=1))
    slow = MockSynthesizer(latency_ms=50)
    with pytest.raises(SpeechTimeout):  # slower than the timeout
        run(slow.synthesize(REQUEST, timeout_s=0.01))
    assert run(slow.synthesize(REQUEST, timeout_s=1)).duration_ms == 2000


def test_fault_plan_syntax() -> None:
    assert [f.kind for f in TtsFaultPlan.parse("error, timeout:2").faults] == ["error", "timeout"]
    for bad in ("boom", "error:0", "error:x", "error:1:2"):
        with pytest.raises(ValueError):
            TtsFaultPlan.parse(bad)


def test_mock_recognizer() -> None:
    recognizer = MockRecognizer()
    result = asyncio.run(recognizer.recognize(RecognitionRequest(b"", "vi", 1200), timeout_s=1))
    assert (result.text, result.status, result.provider, result.duration_ms) == (
        "Đội nhà áp đảo hiệp 2", "ok", "mock", 1200,
    )  # fmt: skip
    custom = MockRecognizer("The home side dominated")
    en = asyncio.run(custom.recognize(RecognitionRequest(b"", "en", 10), timeout_s=1))
    assert en.text == "The home side dominated" and en.language == "en"
