"""Studio Guardrail (docs/day3-design.md section 12): case tables for every new code and
mutation tests on the templates of the four sample matches."""

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from studio_helpers import SAMPLES, first_trigger, pack, triggers

from pundit.agents.commentary import render_texts
from pundit.agents.contracts import FactPack, Lang, Register
from pundit.agents.studio_guard import StudioProduct, StudioScope, check_studio, scope_of
from pundit.agents.studio_text import player_name, team_name
from pundit.agents.talking_points import render_lines


def codes(
    text: str,
    built: FactPack,
    *,
    language: Lang,
    product: StudioProduct = "commentary",
    mode: Register | None = "analyst",
    model_text: bool = True,
) -> set[str]:
    report = check_studio(
        text, scope_of(built), language=language, product=product, mode=mode, model_text=model_text
    )
    return {v.code for v in report.violations}


def saved_shot() -> FactPack:
    trigger = first_trigger("dominant_loser", 42, "shot", outcome="saved")
    return pack("dominant_loser", 42, trigger.id)


def goal() -> FactPack:
    return pack("dominant_loser", 42, first_trigger("dominant_loser", 42, "goal").id)


# --------------------------------------------------------------------------- case tables


def test_templates_pass_and_verdict_words_are_allowed() -> None:
    built = saved_shot()
    for text in render_texts(built):
        assert not codes(text.text, built, language=text.language, mode=text.mode), text.text
    shooter = player_name(built.match, built.trigger.roles["shooter"])
    for line in (f"Một sai lầm nhỏ, nhưng {shooter} đã dứt điểm đúng lúc.",):
        found = codes(line, built, language="vi", mode="casual", model_text=False)
        assert "UNSAFE_CONTENT" not in found and "UNVERIFIED_NUMBER" not in found


def test_wrong_entity() -> None:
    built = saved_shot()
    involved = set(scope_of(built).players)
    stranger = next(p for p in built.match.players if p.id not in involved)
    text = f"{stranger.short_name} dứt điểm nhưng thủ môn cản phá."
    assert "WRONG_ENTITY" in codes(text, built, language="vi", mode="casual")
    shooter = player_name(built.match, built.trigger.roles["shooter"])
    assert "WRONG_ENTITY" not in codes(
        f"{shooter} dứt điểm nhưng thủ môn cản phá.", built, language="vi", mode="casual"
    )


@pytest.mark.parametrize(
    ("text", "language", "wrong"),
    [
        ("{p} ghi bàn!", "vi", True),
        ("{p} scores!", "en", True),
        ("{p} dứt điểm, thủ môn cứu thua.", "vi", False),
        ("Cú sút có bàn thắng kỳ vọng thấp.", "vi", False),  # "bàn thắng kỳ vọng" is xG
        ("Low expected goals for that one.", "en", False),
    ],
)
def test_wrong_outcome_on_a_saved_shot(text: str, language: Lang, wrong: bool) -> None:
    built = saved_shot()
    shooter = player_name(built.match, built.trigger.roles["shooter"])
    found = codes(text.format(p=shooter), built, language=language, mode="casual")
    assert ("WRONG_OUTCOME" in found) is wrong, found


def test_a_goal_is_not_wide() -> None:
    built = goal()
    scorer = player_name(built.match, built.trigger.roles["scorer"])
    assert "WRONG_OUTCOME" in codes(f"{scorer} sút chệch.", built, language="vi", mode="casual")


def test_wrong_language_needs_enough_words() -> None:
    built = saved_shot()
    team = team_name(built.match, built.trigger.team or built.match.teams[0].id)
    assert "WRONG_LANGUAGE" in codes(
        f"{team} takes a shot and the keeper saves it.", built, language="vi", mode="casual"
    )
    assert "WRONG_LANGUAGE" not in codes("xG!", built, language="vi", mode="casual")
    assert "WRONG_LANGUAGE" not in codes(
        f"{team} takes a shot and the keeper saves it.",
        built,
        language="vi",
        mode="casual",
        model_text=False,  # templates are checked by tests, not at run time
    )


def test_wrong_register() -> None:
    built = goal()
    xg = next(f for f in built.facts if f.key.startswith("shot_xg:")).display
    score = "-".join(str(built.match.score[t.id]) for t in built.match.teams)
    two_numbers = f"Bàn thắng với xG {xg}, tỷ số {score}."
    assert "WRONG_REGISTER" in codes(two_numbers, built, language="vi", mode="casual")
    assert "WRONG_REGISTER" not in codes(two_numbers, built, language="vi", mode="analyst")
    assert "WRONG_REGISTER" in codes(
        "Một pha dứt điểm có momentum.", built, language="vi", mode="casual"
    )
    assert "WRONG_REGISTER" in codes(
        "Bàn thắng đẹp mắt cho đội khách.", built, language="vi", mode="analyst"
    )


def test_ungrounded_lines() -> None:
    built = goal()
    team = team_name(built.match, built.trigger.team or "")
    assert "UNGROUNDED" in codes(
        f"{team} is the better side.", built, language="en", product="talking_point", mode=None
    )
    assert "UNGROUNDED" in codes(f"{team} áp đảo hoàn toàn.", built, language="vi", mode="casual")


def test_product_length_limits() -> None:
    built = goal()
    long_line = "Harbor " + "rất " * 60 + "hay."
    assert "TOO_LONG" in codes(long_line, built, language="vi", mode="casual", model_text=False)
    title = "A" * 40
    report = check_studio(title, scope_of(built), language="en", product="graphic", part="title")
    assert "TOO_LONG" in {v.code for v in report.violations}


# --------------------------------------------------------------------------- mutation tests


def _first_number(text: str) -> re.Match[str] | None:
    return re.search(r"\d+(?:\.\d+)?", text)


@pytest.mark.parametrize(("scenario", "seed"), SAMPLES)
def test_mutations_of_the_templates_are_caught(scenario: str, seed: int) -> None:
    """Change a number, name another player, say "scores" on a saved shot, swap the teams:
    the Guardrail rejects every mutation of every template line that carries the target."""
    caught = {"number": 0, "player": 0, "outcome": 0, "teams": 0}
    for trigger in triggers(scenario, seed):
        built = pack(scenario, seed, trigger.id)
        scope = scope_of(built)
        involved = set(scope.players)
        stranger = next(p for p in built.match.players if p.id not in involved)
        home, away = (team_name(built.match, t.id) for t in built.match.teams)
        lines = [(line.text, lang) for lang in ("vi", "en") for line in render_lines(built, lang)]
        lines += [(t.text, t.language) for t in render_texts(built) if t.mode == "analyst"]
        for text, language in lines:

            def check(
                mutated: str, *, language: Lang = language, scope: StudioScope = scope
            ) -> set[str]:
                report = check_studio(
                    mutated, scope, language=language, product="talking_point", mode=None
                )
                return {v.code for v in report.violations}

            number = _first_number(text)
            if number is not None and not text[number.end() :].startswith(("'", "+")):
                value = number.group()
                bumped = (
                    str(int(float(value)) + 37) if "." not in value else f"{float(value) + 3.7:.2f}"
                )
                minute_context = text[max(0, number.start() - 5) : number.start()]
                if (
                    "phút" not in minute_context
                    and "-" not in text[number.end() : number.end() + 1]
                ):
                    mutated = text[: number.start()] + bumped + text[number.end() :]
                    assert "UNVERIFIED_NUMBER" in check(mutated), (text, mutated)
                    caught["number"] += 1
            for player_id in sorted(trigger.roles.values()):
                name = player_name(built.match, player_id)
                if name in text:
                    mutated = text.replace(name, stranger.short_name, 1)
                    assert "WRONG_ENTITY" in check(mutated), (text, mutated)
                    caught["player"] += 1
                    break
            if trigger.outcome in ("saved", "blocked", "off_target", "post"):
                word = " ghi bàn" if language == "vi" else " scores"
                mutated = text.rstrip(".!") + "," + word + "."
                assert "WRONG_OUTCOME" in check(mutated), mutated
                caught["outcome"] += 1
            if home in text and away in text and any(c.isdigit() for c in text):
                swapped = text.replace(home, "\0").replace(away, home).replace("\0", away)
                pair_score = re.search(r"\d+-\d+", text)
                if swapped != text and pair_score is None:
                    found = check(swapped)
                    if not found & {"WRONG_ATTRIBUTION", "UNVERIFIED_NUMBER", "WRONG_COMPARISON"}:
                        # a symmetric line (both values equal) stays true when swapped
                        values = re.findall(r"\d+(?:\.\d+)?%?", text)
                        assert len(set(values)) < len(values), (text, swapped)
                    else:
                        caught["teams"] += 1
    assert all(count > 0 for count in caught.values()), caught


# --------------------------------------------------------------------------- P6 findings (Ollama)


def _pack_at(minute: str, kind: str) -> FactPack:
    trigger = next(
        t for t in triggers("dominant_loser", 42) if t.display_minute == minute and t.kind == kind
    )
    return pack("dominant_loser", 42, trigger.id)


@pytest.mark.parametrize(
    ("minute", "kind", "language", "mode", "text", "caught"),
    [
        # real drafts of qwen2.5:3b (section 23.5): a real number under the wrong metric name
        ("8'", "dangerous_spell", "en", "casual",
         "Harbor FC had 4 shots in the box, Kestrel had none.", True),
        ("19'", "momentum_shift", "vi", "analyst",
         "Trong 10 phút gần nhất, thế trận đang nghiêng về Kestrel Athletic với chỉ số hỗn loạn "
         "0.88, trong khi Kestrel Athletic kiểm soát bóng đạt 42%.", True),
        ("27'", "dangerous_spell", "vi", "analyst",
         "Kestrel Athletic có 4 lần đưa bóng vào vòng cấm, trong khi chỉ có 1 cú sút vào vòng cấm "
         "của Harbor Lions FC.", True),
        ("27'", "dangerous_spell", "en", "casual",
         "Kestrel kept pressing with four shots in the box.", True),
        # the same numbers under their own names pass
        ("8'", "dangerous_spell", "en", "analyst",
         "Harbor Lions FC kept up the pressure with 3 box entries and 4 penalty-area entries in "
         "the last 7 minutes, while Kestrel Athletic had none.", False),
        ("19'", "momentum_shift", "en", "analyst",
         "The momentum swings to Kestrel Athletic, with a possession ratio of 42% compared to "
         "Harbor Lions FC's 58% in the last 10 minutes.", False),
        ("27'", "dangerous_spell", "en", "analyst",
         "Kestrel Athletic kept their pressure with two shots and four entries into the penalty "
         "area in the last 10 minutes.", False),
    ],
)  # fmt: skip
def test_a_number_under_the_wrong_metric_name_is_caught(
    minute: str, kind: str, language: Lang, mode: Register, text: str, caught: bool
) -> None:
    found = codes(text, _pack_at(minute, kind), language=language, mode=mode)
    assert ("UNVERIFIED_NUMBER" in found) is caught, found


@pytest.mark.parametrize(
    ("text", "caught"),
    [
        (
            "E. Brandvold's left-foot shot was off target, while J. Ellery had one shot as well.",
            True,
        ),
        ("J. Ellery had a shot, but it was off target. E. Brandvold also missed his chance.", True),
        ("J. Ellery shoots and E. Brandvold watches it go wide.", False),
    ],
)
def test_the_goalkeeper_of_a_chance_never_shoots(text: str, caught: bool) -> None:
    found = codes(text, _pack_at("9'", "big_chance"), language="en", mode="casual")
    assert ("WRONG_ENTITY" in found) is caught, found


def test_a_unit_says_what_the_number_is() -> None:
    rocket = _pack_at("8'", "rocket_shot")
    speed = next(f.display for f in rocket.facts if f.key.startswith("shot_speed:"))
    shooter = player_name(rocket.match, rocket.trigger.roles["shooter"])
    text = f"Phút 8: {shooter} tung cú sút {speed}, bóng đi chệch khung thành."
    assert not codes(text, rocket, language="vi", mode="analyst")


@pytest.mark.parametrize(
    ("minute", "kind", "language", "text", "code"),
    [
        # the goalkeeper or another player named as the scorer
        ("27'", "goal", "vi",
         "Tới phút 27, E. Brandvold ghi bàn cho Kestrel Athletic.", "WRONG_ENTITY"),
        # ordinal goal counts are numbers too
        ("63'", "goal", "vi",
         "T. Calder đã ghi bàn vào phút 63. Đây là bàn thắng thứ ba của Calder trong trận đấu.",
         "UNVERIFIED_NUMBER"),
        ("78'", "goal", "en",
         "J. Ellery scored his second goal of the match in the 78th minute.", "UNVERIFIED_NUMBER"),
        # what never happens in these matches, or not at this moment
        ("90+6'", "full_time", "vi",
         "Kestrel Athletic vẫn thắng 2-1 sau hiệp phụ.", "WRONG_OUTCOME"),
        ("63'", "goal", "en",
         "T. Calder scored his first goal, and M. Holub made an own goal save.", "WRONG_OUTCOME"),
        ("78'", "goal", "vi",
         "J. Ellery ghi bàn, Kestrel Athletic giành chiến thắng 2-1.", "WRONG_OUTCOME"),
        ("68'", "big_chance", "en",
         "D. Esposti's shot went off target. M. Holub kept it out.", "WRONG_OUTCOME"),
        # a summary line written by a model does not tell the goals
        ("45+3'", "period_end", "vi",
         "Kestrel Athletic ghi bàn ở phút 45+3, mở tỷ số trận đấu.", "WRONG_OUTCOME"),
        ("90+6'", "full_time", "en", "Harbor scored first but Kestrel won 2-1 in the end.",
         "WRONG_OUTCOME"),
    ],
)  # fmt: skip
def test_narrative_errors_of_model_drafts_are_caught(
    minute: str, kind: str, language: Lang, text: str, code: str
) -> None:
    found = codes(text, _pack_at(minute, kind), language=language, mode="casual")
    assert code in found, found


@pytest.mark.parametrize(
    ("minute", "kind", "language", "mode", "text"),
    [
        ("27'", "goal", "en", "casual",
         "M. Ribas scored his first goal of the match in the 27th minute."),
        ("78'", "goal", "vi", "casual",
         "J. Ellery ghi bàn thắng thứ hai cho Kestrel Athletic ở phút 78."),
        ("90+6'", "full_time", "en", "casual", "Kestrel Athletic won the match 2-1."),
        # "một" is an article here, not the number 1
        ("55'", "big_chance", "vi", "analyst",
         "O. Lindqvist tạo ra một cơ hội ngon ăn với xG 0.36, nhưng cú sút bị chặn."),
    ],
)  # fmt: skip
def test_true_drafts_are_kept(
    minute: str, kind: str, language: Lang, mode: Register, text: str
) -> None:
    assert not codes(text, _pack_at(minute, kind), language=language, mode=mode)


def test_templates_may_tell_goals_in_summaries_but_models_may_not() -> None:
    period_end = _pack_at("45+3'", "period_end")
    line = "Kestrel Athletic ghi bàn ở phút 45+3."
    assert "WRONG_OUTCOME" in codes(line, period_end, language="vi", mode="casual")
    template = codes(line, period_end, language="vi", mode="casual", model_text=False)
    assert "WRONG_OUTCOME" not in template


def test_mot_counts_before_a_statistic() -> None:
    """ "Chỉ có một cú sút" still says 1 shot: only "một cơ hội / pha tấn công / ..." is an
    article."""
    chance = _pack_at("55'", "big_chance")
    found = codes(
        "Harbor Lions FC chỉ có một cú sút trúng đích.", chance, language="vi", mode="analyst"
    )
    assert "UNVERIFIED_NUMBER" in found


@pytest.mark.parametrize(
    ("minute", "language", "text"),
    [
        ("55'", "en", "The xG for the shot was 0.36."),
        ("68'", "vi", "xG của cú sút là 0.34."),
    ],
)
def test_a_decimal_is_never_a_count(minute: str, language: Lang, text: str) -> None:
    """P6 round 2: "shot" is the nearest metric word, but 0.36 cannot be a number of shots."""
    built = _pack_at(minute, "big_chance")
    assert not codes(text, built, language=language, product="talking_point", mode=None)


# --------------------------------------------------------------------------- P6 final run (Ollama)


@pytest.mark.parametrize(
    ("minute", "kind", "language", "text", "wrong"),
    [
        # real drafts of the final run: a lead at a level score, a lead of the side behind
        ("63'", "goal", "vi",
         "Tài năng của T. Calder đã đưa Harbor Lions FC vượt lên dẫn trước với bàn thắng ở phút "
         "63.", True),
        ("90+3'", "dangerous_spell", "en",
         "In the last 10 minutes, Harbor Lions FC had 9 box entries and Kestrel Athletic had "
         "none, maintaining their lead.", True),
        ("78'", "goal", "vi", "J. Ellery gỡ hòa cho Kestrel Athletic ở phút 78.", True),
        ("50'", "momentum_shift", "vi",
         "Kestrel Athletic đang bị dẫn trước, Harbor Lions FC kiểm soát bóng 56%.", True),
        # the same words where they fit the score, or where the lead was lost
        ("78'", "goal", "en",
         "J. Ellery scored in the 78th minute, giving Kestrel Athletic a 2-1 lead.", False),
        ("27'", "goal", "en", "M. Ribas scored, putting Kestrel Athletic ahead 1-0.", False),
        ("63'", "goal", "en", "T. Calder equalises for Harbor Lions FC: 1-1.", False),
        ("63'", "goal", "en", "T. Calder cancels out Kestrel Athletic's lead.", False),
        ("50'", "momentum_shift", "vi",
         "Harbor Lions FC đang bị dẫn trước nhưng kiểm soát bóng 56%.", False),
    ],
)  # fmt: skip
def test_lead_and_level_words_follow_the_score(
    minute: str, kind: str, language: Lang, text: str, wrong: bool
) -> None:
    found = codes(text, _pack_at(minute, kind), language=language, mode="analyst")
    assert ("WRONG_OUTCOME" in found) is wrong, found


@pytest.mark.parametrize(
    ("minute", "kind", "text", "wrong"),
    [
        ("49'", "dangerous_spell",
         "Harbor Lions FC had 4 penalty-area entries in the last 10 minutes. This sustained "
         "pressure has led to the goal.", True),
        ("90+3'", "dangerous_spell",
         "Harbor Lions FC had 9 box entries in the last 10 minutes, leading to Harbor's goal.",
         True),
        ("56'", "dangerous_spell",
         "Harbor Lions FC had 8 penalty-area entries in the last 10 minutes, which led to a shot "
         "on goal.", False),
        ("63'", "goal", "Harbor Lions FC's pressure led to the goal: T. Calder scored for 1-1.",
         False),
    ],
)  # fmt: skip
def test_a_goal_caused_where_nobody_scored(minute: str, kind: str, text: str, wrong: bool) -> None:
    found = codes(text, _pack_at(minute, kind), language="en", mode="analyst")
    assert ("WRONG_OUTCOME" in found) is wrong, found


@pytest.mark.parametrize(
    ("minute", "kind", "language", "text", "wrong"),
    [
        ("50'", "momentum_shift", "en",
         "The momentum is swinging strongly in favor of Kestrel Athletic, with a possession "
         "advantage of 56% to 44% in the last 10 minutes.", True),
        ("56'", "dangerous_spell", "vi",
         "Harbor Lions FC đang chịu áp lực liên tục, với 8 lần vào vòng cấm trong 10 phút gần "
         "nhất.", True),
        ("49'", "dangerous_spell", "en",
         "Harbor Lions FC are under a lot of pressure, with 4 penalty-area entries.", True),
        ("19'", "momentum_shift", "en",
         "The momentum is swinging strongly in favor of Kestrel Athletic, with a possession "
         "ratio of 42% to 58% in the last 10 minutes.", False),
        ("56'", "dangerous_spell", "vi",
         "Kestrel Athletic đang chịu áp lực: Harbor Lions FC có 8 lần vào vòng cấm trong 10 phút "
         "gần nhất.", False),
        ("49'", "dangerous_spell", "en",
         "Harbor Lions FC put Kestrel Athletic under pressure with 4 penalty-area entries.",
         False),
    ],
)  # fmt: skip
def test_the_side_on_top_of_a_momentum_shift_or_a_spell(
    minute: str, kind: str, language: Lang, text: str, wrong: bool
) -> None:
    found = codes(text, _pack_at(minute, kind), language=language, mode="analyst")
    assert ("WRONG_ATTRIBUTION" in found) is wrong, found


@pytest.mark.parametrize(
    ("language", "text", "wrong"),
    [
        ("en", "O. Lindqvist had a shot that was blocked by Kestrel Athletic's goalkeeper "
               "M. Holub.", True),
        ("en", "O. Lindqvist shot but M. Holub blocked it.", True),
        ("vi", "Cú sút của O. Lindqvist bị M. Holub chặn lại.", True),
        ("en", "O. Lindqvist's shot was blocked; M. Holub was not needed.", False),
        ("vi", "Cú sút của O. Lindqvist bị chặn lại trước khi tới chỗ M. Holub.", False),
    ],
)  # fmt: skip
def test_the_goalkeeper_never_blocks_a_blocked_shot(language: Lang, text: str, wrong: bool) -> None:
    found = codes(text, _pack_at("55'", "big_chance"), language=language, mode="casual")
    assert ("WRONG_ENTITY" in found) is wrong, found


@pytest.mark.parametrize(
    ("minute", "kind", "product", "text", "wrong"),
    [
        ("9'", "big_chance", "commentary",
         "J. Ellery có một cơ hội ngon ăn với xG 0.35, nhưng cú sút của anh lại đi off target.",
         True),
        ("49'", "dangerous_spell", "talking_point",
         "a spell of sustained pressure by Harbor Lions FC trong phút 49, tỷ số 0-1 Kestrel "
         "Athletic", True),
        ("90+6'", "full_time", "talking_point",
         "Harbor Lions FC 1-2 Kestrel Athletic tại full time.", True),
        ("9'", "big_chance", "commentary",
         "J. Ellery có cơ hội ngon ăn với xG 0.35, nhưng sút ra ngoài.", False),
        ("56'", "dangerous_spell", "commentary",
         "Harbor Lions FC pressing tầm cao, 8 lần vào vòng cấm trong 10 phút gần nhất.", False),
    ],
)  # fmt: skip
def test_english_words_inside_a_vietnamese_line(
    minute: str, kind: str, product: StudioProduct, text: str, wrong: bool
) -> None:
    mode: Register | None = "analyst" if product == "commentary" else None
    built = _pack_at(minute, kind)
    found = codes(text, built, language="vi", product=product, mode=mode)
    assert ("WRONG_LANGUAGE" in found) is wrong, found
    template = codes(text, built, language="vi", product=product, mode=mode, model_text=False)
    assert "WRONG_LANGUAGE" not in template  # a model rule


@pytest.mark.parametrize(
    ("minute", "kind", "text", "wrong"),
    [
        ("68'", "big_chance",
         "D. Esposti có cơ hội ngon ăn, nhưng sút ra ngoài. Haror Lions FC mất một cơ hội tốt.",
         True),
        ("50'", "momentum_shift", "Thế trận nghiêng hẳn về Harbour Lions FC, kiểm soát bóng 56%.",
         True),
        ("78'", "goal", "J. Ellery của Kestrel Athletic FC ghi bàn, nâng tỷ số lên 2-1.", False),
    ],
)  # fmt: skip
def test_a_misspelled_team_name(minute: str, kind: str, text: str, wrong: bool) -> None:
    found = codes(text, _pack_at(minute, kind), language="vi", mode="casual")
    assert ("WRONG_ENTITY" in found) is wrong, found


@pytest.mark.parametrize(
    ("minute", "language", "text", "kept"),
    [
        # wrong rejections found in the drafts of the final run: xG written out, "là một"
        ("68'", "vi",
         "Bàn thắng kỳ vọng của anh ấy là 0.34, còn cả trận Kestrel Athletic có 0.72 bàn thắng "
         "kỳ vọng.", True),
        ("55'", "vi", "Đây là một cú sút xa từ ngoài vòng cấm, xG 0.36.", True),
        ("9'", "en", "Kestrel Athletic had a 0.35 chance of scoring.", True),
        # a decimal next to a count noun is still not a count
        ("55'", "vi", "Harbor Lions FC có 0.87 cú sút trong trận.", False),
        ("55'", "vi", "Harbor Lions FC chỉ có một cú sút trúng đích.", False),
    ],
)  # fmt: skip
def test_xg_in_words_and_the_article_mot(
    minute: str, language: Lang, text: str, kept: bool
) -> None:
    found = codes(text, _pack_at(minute, "big_chance"), language=language, mode="analyst")
    assert ("UNVERIFIED_NUMBER" not in found) is kept, found


STUDIO_VOCAB = [
    "Harbor Lions FC", "Kestrel Athletic", "Haror", "Harbour", "M. Holub", "T. Calder", "their",
    "lead", "the lead", "ahead", "trailing", "dẫn trước", "bị", "gỡ hòa", "hòa 1-1", "equalise",
    "led to", "the goal", "in favor of", "nghiêng về", "under pressure", "chịu áp lực",
    "blocked by", "chặn", "off", "target", "full time", "một", "là", "cú sút", "0.72",
    "bàn thắng kỳ vọng", "chance", "1-2", "63'", ",", ".", ";", "xG", "VAR", "km/h", "49th",
]  # fmt: skip


@settings(max_examples=150, deadline=None)
@given(
    st.lists(st.sampled_from(STUDIO_VOCAB), max_size=14),
    st.text(max_size=20),
    st.sampled_from(["vi", "en"]),
)
def test_fuzz_the_studio_guard_never_crashes(words: list[str], noise: str, language: Lang) -> None:
    text = " ".join(words) + " " + noise
    for minute, kind in (("55'", "big_chance"), ("50'", "momentum_shift"), ("63'", "goal")):
        report = check_studio(
            text,
            scope_of(_pack_at(minute, kind)),
            language=language,
            product="commentary",
            mode="analyst",
            model_text=True,
        )
        assert report.passed == (not report.violations)


# ------------------------------------------- Day 6: precision (studio audit with Ollama)


@pytest.mark.parametrize(
    ("trigger", "text", "language", "product", "mode"),
    [
        ("red_card_swing-7-m0040", "Goalie saved the shooter's shot. The shot had a 0.4 chance "
         "of going in.", "en", "commentary", "casual"),
        ("red_card_swing-7-m0040", "Lindqvist's shot was a 0.4 xG chance.", "en", "commentary",
         "analyst"),
    ],
)  # fmt: skip
def test_a_number_coarser_than_its_fact_on_screen_is_refused(
    trigger: str, text: str, language: Lang, product: StudioProduct, mode: Register
) -> None:
    """What Ollama published in the studio audit of Day 6: the xG on screen is 0.44; the studio
    oracle and the independent number audit refused the coarser 0.4, so the guardrail does too."""
    built = pack("red_card_swing", 7, trigger)
    assert "UNVERIFIED_NUMBER" in codes(text, built, language=language, product=product, mode=mode)


def test_the_number_as_shown_passes() -> None:
    built = pack("red_card_swing", 7, "red_card_swing-7-m0040")
    text = "M. Holub saved O. Lindqvist's shot. The shot had an xG of 0.44."
    assert "UNVERIFIED_NUMBER" not in codes(text, built, language="en")
    # "1.5" for an xG shown 1.50 is the same number: the oracle flagged it, wrongly
    talk = "Kestrel Athletic đã có 1.5 bàn thắng kỳ vọng trong trận đấu."
    built = pack("red_card_swing", 7, "red_card_swing-7-m0005")
    assert "UNVERIFIED_NUMBER" not in codes(talk, built, language="vi", product="talking_point")
