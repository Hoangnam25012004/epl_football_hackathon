"""Studio Guardrail (docs/day3-design.md section 12): case tables for every new code and
mutation tests on the templates of the four sample matches."""

import re

import pytest
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
