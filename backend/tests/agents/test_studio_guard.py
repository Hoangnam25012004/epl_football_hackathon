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
