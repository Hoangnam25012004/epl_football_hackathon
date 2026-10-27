"""Prosody presets and SSML (docs/day5-design.md section 6): the verified limits of Azure, a
golden document, escaping of any text, the checker's refusals, clip ids and estimates."""

import xml.etree.ElementTree as ET

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pundit.agents.contracts import SpeechSegment
from pundit.agents.prosody import PRESETS, calmer, preset_for
from pundit.agents.ssml import billable_chars, build_ssml, check_ssml, clip_id, estimate_ms
from pundit.agents.voices import voice_for

VOICE = voice_for("vi", "female")


def segment(text: str, preset: str = "calm", pause: int = 0) -> SpeechSegment:
    p = PRESETS[preset]  # type: ignore[index]
    return SpeechSegment(
        text=text, preset=preset, rate=p.rate, pitch=p.pitch, volume=p.volume,  # type: ignore[arg-type]
        pause_before_ms=pause,
    )  # fmt: skip


def test_presets_stay_within_the_limits_of_azure() -> None:
    for name, prosody in PRESETS.items():
        assert 0.5 <= prosody.rate <= 2.0, name  # rate 0.5-2x
        assert 0.5 <= prosody.pitch <= 1.5, name  # pitch 0.5-1.5x
        assert prosody.volume_pct <= 0 and 0 < prosody.volume <= 1, name  # 100 is the maximum
    assert PRESETS["peak"].rate > PRESETS["excited"].rate > PRESETS["lively"].rate
    assert PRESETS["burst"].pitch > PRESETS["peak"].pitch > PRESETS["calm"].pitch
    assert PRESETS["explainer"].rate < 1 and PRESETS["calm"].volume < PRESETS["peak"].volume
    assert PRESETS["calm"].rate > 1  # 28/10: a caster's pace even for ordinary lines


def test_preset_of_a_trigger() -> None:
    assert [preset_for("goal", e) for e in range(4)] == ["calm", "lively", "excited", "peak"]
    assert preset_for("goal_overturned", 3) == preset_for("var_review", 0) == "tension"
    assert preset_for("stat_tick", 9) == "peak" and preset_for("shot", -1) == "calm"
    assert [calmer(p) for p in ("peak", "excited", "lively", "tension")] == [
        "excited", "lively", "lively", "tension",
    ]  # fmt: skip


def test_golden_ssml() -> None:
    ssml = build_ssml([segment("Vào!", "burst"), segment("Ellery ghi bàn!", "peak", 250)], VOICE)
    assert ssml == (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="vi-VN">'
        '<voice name="vi-VN-HoaiMyNeural">'
        '<prosody rate="+30%" pitch="+20%">Vào!</prosody><break time="250ms"/>'
        '<prosody rate="+25%" pitch="+14%">Ellery ghi bàn!</prosody></voice></speak>'
    )
    assert check_ssml(ssml, VOICE) == []
    calm = build_ssml([segment("Phút chín.")], VOICE)
    assert '<prosody rate="+8%" pitch="+2%" volume="-5%">Phút chín.</prosody>' in calm
    tension = build_ssml([segment("VAR.", "tension")], VOICE)
    assert '<prosody rate="-2%" pitch="-3%">VAR.</prosody>' in tension  # only what changes
    assert build_ssml([segment("Chào.", "recap")], voice_for("vi", "male")).count("NamMinh") == 1


@given(st.text(max_size=80))
def test_any_text_is_escaped_into_valid_ssml(text: str) -> None:
    words = text.strip() or "x"
    ssml = build_ssml([segment(words), segment(words, "peak", 100)], VOICE)
    assert check_ssml(ssml, VOICE) == []
    root = ET.fromstring(ssml)
    assert len(list(root)) == 1


@pytest.mark.parametrize(
    ("ssml", "problem"),
    [
        ("<speak", "does not parse"),
        ('<!DOCTYPE x><speak xml:lang="vi-VN"/>', "declaration"),
        (
            '<speak xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-GB">'
            '<voice name="vi-VN-HoaiMyNeural">x</voice></speak>',
            "xml:lang",
        ),
        (
            '<speak xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="vi-VN">'
            '<voice name="vi-VN-HoaiMyNeural"><audio src="https://x"/></voice></speak>',
            "not allowed",
        ),
        (
            '<speak xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="vi-VN">'
            '<voice name="vi-VN-HoaiMyNeural"><break time="5000ms"/></voice></speak>',
            "bad break",
        ),
        (
            '<speak xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="vi-VN">'
            '<voice name="vi-VN-HoaiMyNeural"><prosody rate="fast" contour="x">a</prosody>'
            "</voice></speak>",
            "attributes",
        ),
        (
            '<speak xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="vi-VN">'
            '<voice name="vi-VN-NamMinhNeural">a</voice></speak>',
            "expected vi-VN-HoaiMyNeural",
        ),
        (
            '<speak xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="vi-VN">text'
            '<voice name="vi-VN-HoaiMyNeural">a</voice></speak>',
            "outside the voice",
        ),
    ],
)
def test_checker_refuses(ssml: str, problem: str) -> None:
    assert any(problem in p for p in check_ssml(ssml, VOICE)), check_ssml(ssml, VOICE)


def test_size_limit_and_billing() -> None:
    long = build_ssml([segment("a" * 11_000)], VOICE)
    assert any("larger" in p for p in check_ssml(long, VOICE))
    ssml = build_ssml([segment("Vào!", "burst")], VOICE)
    assert billable_chars(ssml) == len('<prosody rate="+30%" pitch="+20%">Vào!</prosody>')


def test_clip_ids_name_the_content() -> None:
    ssml = build_ssml([segment("Phút chín.")], VOICE)
    first = clip_id("azure", VOICE.name, "audio-24khz-48kbitrate-mono-mp3", ssml)
    assert first == clip_id("azure", VOICE.name, "audio-24khz-48kbitrate-mono-mp3", ssml)
    assert len(first) == 32 and int(first, 16) >= 0
    assert first != clip_id("mock", VOICE.name, "audio-24khz-48kbitrate-mono-mp3", ssml)
    assert first != clip_id("azure", VOICE.name, "audio-24khz-48kbitrate-mono-mp3", ssml + " ")
    # sha256, not hash(): the same id in every process (a restarted app finds its clips)
    assert first == "e94f607757300d7d57cb9376e97ea729"  # 28/10: the calm preset changed


def test_estimate_follows_text_rate_and_pauses() -> None:
    rate = PRESETS["calm"].rate
    base = estimate_ms([segment("a" * 150)], "vi")
    assert base == pytest.approx(10_000 / rate, abs=1)  # 15 characters per second, at its rate
    assert estimate_ms([segment("a" * 150, "peak")], "vi") < base
    both = estimate_ms([segment("a" * 150), segment("b" * 15, "calm", 700)], "vi")
    assert both == pytest.approx(11_000 / rate + 700, abs=1)
