"""Spoken scripts and SpeechGuard (docs/day5-design.md sections 4 and 7): what is read for a line,
a recap and the greeting, and every guard rule refusing what must never be read."""

from typing import Any

from helpers import match_context
from studio_helpers import offline

from pundit.agents.contracts import CommentaryView, Profile, RuleView
from pundit.agents.narration import (
    NARRATION,
    MomentLine,
    ScriptPart,
    SpeechScript,
    commentary_script,
    greeting_script,
    recap_moment_lines,
    recap_script,
    split_burst,
)
from pundit.agents.personalize import render_recap
from pundit.agents.speech_cue import RECAP_TARGET_MS, draft_cue, draft_recap, fit_recap
from pundit.agents.spoken import EMPTY_LEXICON, SpokenLexicon
from pundit.agents.voices import voice_for

LEX = SpokenLexicon.from_match(match_context("dominant_loser", 42))
FEMALE = voice_for("vi", "female")


def view(**values: Any) -> CommentaryView:
    base: dict[str, Any] = {
        "id": "x-m0001-c", "trigger_id": "x-m0001", "kind": "goal", "period": 2,
        "clock_ms": 1_000, "display_minute": "63'", "importance": 3, "language": "vi",
        "mode": "casual", "text": "Bàn thắng! T. Calder đưa bóng vào lưới.", "speak": True,
        "excitement": 3, "source": "template",
    }  # fmt: skip
    base.update(values)
    return CommentaryView(**base)


def test_split_burst() -> None:
    assert split_burst("VÀOOO! J. Ellery ghi bàn.") == ("VÀOOO!", "J. Ellery ghi bàn.")
    assert split_burst("Suýt nữa! Bóng đi chệch.") == ("Suýt nữa!", "Bóng đi chệch.")
    assert split_burst("Một pha bóng rất đẹp mắt và nguy hiểm! Rồi.")[0] is None  # > 4 words
    assert split_burst("Hết hiệp 1!") == (None, "Hết hiệp 1!")  # nothing after it
    assert split_burst("Phút 9. Vào!")[0] is None  # the first sentence is not an exclamation


def test_commentary_script_parts() -> None:
    script = commentary_script(view(framing="Đội của bạn ghi bàn!"))
    assert script is not None
    assert [(p.source, p.preset, p.pause_before_ms) for p in script.parts] == [
        ("framing", "peak", 0), ("commentary", "burst", 150), ("commentary", "peak", 250),
    ]  # fmt: skip
    assert script.main_preset == "peak"
    calm = commentary_script(view(kind="stat_tick", excitement=0, text="Sau 15 phút: x."))
    assert calm is not None and [p.preset for p in calm.parts] == ["calm"]
    assert commentary_script(view(speak=False)) is None


def test_a_beginner_hears_the_short_rule() -> None:
    explanation = RuleView(rule="offside", title="Việt vị", text="(bản đầy đủ)", depth="full")
    script = commentary_script(view(kind="shot", excitement=0, explanation=explanation))
    assert script is not None
    rule = script.parts[-1]
    assert rule.source == "rule" and rule.preset == "explainer" and rule.pause_before_ms == 600
    assert rule.text.startswith("Việt vị:") and len(rule.text) <= 140  # the short text


def test_greeting_says_ai_voice_and_synthetic_data() -> None:
    script = greeting_script("vi")
    assert "AI" in script.parts[0].text and "dữ liệu tổng hợp" in script.parts[0].text
    draft = draft_cue(script, FEMALE, EMPTY_LEXICON)
    assert draft.report.passed and draft.preset == "greeting"
    for phrases in NARRATION.values():  # static text: short, no number of a match
        for text in vars(phrases).values():
            assert len(text) <= 120 and not any(ch.isdigit() for ch in text)


def test_recap_moments_drop_overturned_goals_and_respect_the_scope() -> None:
    run = offline("comeback_var", 7)
    commentary = {o.commentary.id: o.commentary for o in run.outputs}
    overturned = {
        goal for m in run.moments if m.kind == "goal_overturned" for goal in m.related_moment_ids
    }
    assert overturned, "comeback_var-7 has a goal overturned by VAR"
    for scope, recaps in run.recaps.items():
        recap = recaps[-1]
        lines = recap_moment_lines(recap, run.moments, commentary, "vi", "casual")
        assert lines and [line.seq for line in lines] == sorted(line.seq for line in lines)
        assert not {line.moment_id for line in lines} & overturned
        assert all(line.seq <= recap.as_of_seq for line in lines)
        assert len(lines) <= (6 if scope == "H1" else 8)
        if scope == "H1":
            assert all(line.period == 1 for line in lines)


def test_recap_script_and_its_length() -> None:
    run = offline("dominant_loser", 42)
    commentary = {o.commentary.id: o.commentary for o in run.outputs}
    recap = run.recaps["FULL"][-1]
    for mode in ("analyst", "casual"):
        recap_view = render_recap(recap, Profile(language="vi", mode=mode))
        lines = recap_moment_lines(recap, run.moments, commentary, "vi", mode)
        script = recap_script(recap_view, lines, recap.as_of_seq)
        sources = [p.source for p in script.parts]
        assert sources[0] == "phrase" and sources[-1] == "phrase"
        assert "AI" in script.parts[0].text and "tổng hợp" in script.parts[0].text
        assert sources.count("recap") == len(recap_view.paragraphs)
        moments = [p.text for p in script.parts if p.source == "moment"]
        assert all(text.startswith("Phút ") for text in moments)
        draft = draft_recap(recap_view, lines, recap.as_of_seq, FEMALE, LEX)
        assert draft.report.passed, draft.report.problems
        assert 30_000 <= draft.estimate_ms <= RECAP_TARGET_MS


def test_a_long_recap_drops_minor_moments_first() -> None:
    run = offline("comeback_var", 7)
    commentary = {o.commentary.id: o.commentary for o in run.outputs}
    recap = run.recaps["FULL"][-1]
    recap_view = render_recap(recap, Profile(language="vi", mode="analyst"))
    lines = recap_moment_lines(recap, run.moments, commentary, "vi", "analyst")
    assert any(line.major for line in lines) and any(not line.major for line in lines)
    full = draft_cue(recap_script(recap_view, lines, recap.as_of_seq), FEMALE, LEX)
    bare = draft_cue(recap_script(recap_view, [], recap.as_of_seq), FEMALE, LEX)
    for share in (0.2, 0.5, 0.8):  # targets that keep some of the moments, not all
        target = round(bare.estimate_ms + (full.estimate_ms - bare.estimate_ms) * share)
        draft, kept = fit_recap(recap_view, lines, recap.as_of_seq, FEMALE, LEX, target_ms=target)
        assert draft.estimate_ms <= target and len(kept) < len(lines)
        if any(not line.major for line in kept):  # a minor moment stays only with every major
            assert all(line in kept for line in lines if line.major)
        assert list(kept) == [line for line in lines if line in kept]  # match order kept


# --------------------------------------------------------------------------- SpeechGuard


def guard(script: SpeechScript, lexicon: SpokenLexicon = LEX) -> list[str]:
    return draft_cue(script, FEMALE, lexicon).report.codes()


def test_guard_passes_a_real_line() -> None:
    script = commentary_script(view(text="Phút 27, M. Ribas ghi bàn. Harbor Lions FC 0-1 KES."))
    assert script is not None and guard(script) == []


def test_guard_g1_sources() -> None:
    script = SpeechScript("commentary", "vi", (ScriptPart("phrase", "Xin chào.", "calm"),))
    assert guard(script) == ["SOURCE"]


def test_guard_g2_numbers_that_cannot_be_read() -> None:
    part = ScriptPart("commentary", "Mã số 1.2.3 đây.", "calm")
    script = SpeechScript("commentary", "vi", (part,))
    assert "NUMBERS" in guard(script)  # an odd number is left as digits: never read


def test_guard_g3_language() -> None:
    part = ScriptPart("commentary", "The home side scored a fine goal and they lead now.", "calm")
    assert "LANGUAGE" in guard(SpeechScript("commentary", "vi", (part,)))


def test_guard_g4_length() -> None:
    part = ScriptPart("commentary", "Bóng lăn trên sân cỏ. " * 40, "calm")
    assert "LENGTH" in guard(SpeechScript("commentary", "vi", (part,)))
    assert "LENGTH" in guard(SpeechScript("commentary", "vi", ()))


def test_guard_g6_banned_words() -> None:
    part = ScriptPart("commentary", "Hàng thủ thật vô dụng ở pha này.", "calm")
    assert "UNSAFE" in guard(SpeechScript("commentary", "vi", (part,)))


def test_guard_g7_scope_of_a_recap() -> None:
    late = MomentLine("x-m0009", seq=500, period=2, display_minute="70'", text="Bàn thắng.",
                      preset="excited", major=True)  # fmt: skip
    parts = (ScriptPart("phrase", "Tóm tắt hiệp một.", "recap"),)
    early = SpeechScript("recap", "vi", parts, scope="H1", as_of_seq=400, moments=(late,))
    assert guard(early) == ["SCOPE"]
    no_scope = SpeechScript("recap", "vi", parts)
    assert guard(no_scope) == ["SCOPE"]
