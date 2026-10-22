"""Personalization (docs/day3-design.md section 11): profiles, the policy table, club framing,
beginner explanations, player focus, one metric, and the club swap (S-9)."""

import itertools

import pytest
from studio_helpers import offline, view_at

from pundit.agents.contracts import CommentaryItem, Profile, RuleId
from pundit.agents.language import Text
from pundit.agents.numbers import extract_numbers
from pundit.agents.personalize import (
    FRAMING,
    ProfileError,
    metric_facts,
    parse_profile,
    render_commentary,
    render_lower_third,
    render_recap,
    render_stateless,
    tracker_facts,
)
from pundit.agents.studio_guard import stat_number_count

DL = ("dominant_loser", 42)
HOME, AWAY = "harbor_lions", "kestrel_athletic"


def items(scenario: str = DL[0], seed: int = DL[1]) -> list[CommentaryItem]:
    return [output.commentary for output in offline(scenario, seed).outputs]


def item_of(
    kind: str, team: str | None = None, scenario: str = DL[0], seed: int = DL[1]
) -> CommentaryItem:
    return next(
        i
        for i in items(scenario, seed)
        if i.trigger_kind == kind and (team is None or i.team == team)
    )


def context():  # type: ignore[no-untyped-def]
    run = offline(*DL)
    from pundit.agents.context import build_match_context

    return build_match_context(run.view_at(0))


# --------------------------------------------------------------------------- profiles


def test_default_profile_is_analyst_vietnamese() -> None:
    profile = parse_profile({}, context())
    assert profile == Profile() and profile.key() == "analyst|vi|-|-|0|-"


@pytest.mark.parametrize("club", ["HAR", "har", "harbor_lions", "Harbor Lions FC"])
def test_club_by_id_short_or_full_name(club: str) -> None:
    assert parse_profile({"club": club}, context()).team == HOME


@pytest.mark.parametrize(
    "params",
    [
        {"mode": "expert"},
        {"lang": "fr"},
        {"club": "Real Madrid"},
        {"player": "nobody-99"},
        {"beginner": "maybe"},
        {"metric": "chaos_index"},  # a match metric, not a team metric
        {"metric": "nope"},
    ],
)
def test_bad_profiles_are_refused_with_a_reason(params: dict[str, str]) -> None:
    with pytest.raises(ProfileError) as error:
        parse_profile(params, context())
    assert str(error.value)


def test_full_profile() -> None:
    player = f"{HOME}-09"
    params = {"mode": "casual", "lang": "en", "club": "KES", "player": player, "beginner": "1"}
    profile = parse_profile({**params, "metric": "xg"}, context())
    assert profile.key() == f"casual|en|{AWAY}|{player}|1|xg"


# --------------------------------------------------------------------------- policy table 11.2


@pytest.mark.parametrize(
    ("mode", "language", "club", "beginner"),
    list(itertools.product(("analyst", "casual"), ("vi", "en"), (None, HOME), (False, True))),
)
def test_policy_table(mode: str, language: str, club: str | None, beginner: bool) -> None:
    profile = Profile(mode=mode, language=language, team=club, beginner=beginner)  # type: ignore[arg-type]
    offside = item_of("offside")
    view, shown = render_commentary(offside, profile, context(), seen=set())
    assert view.text == offside.text(language, mode).text  # type: ignore[arg-type]
    assert view.language == language and view.mode == mode
    assert bool(view.facts) == (mode == "analyst" and bool(offside.facts))
    assert (view.explanation is not None) == beginner
    assert (shown == "offside") == beginner
    goal = item_of("goal", AWAY)
    goal_view, _ = render_commentary(goal, profile, context(), seen=set())
    assert (goal_view.framing is not None) == (mode == "casual" and club is not None)
    if club is not None:
        assert goal_view.emphasis == "opponent"


def test_framing_lines_have_no_number_and_both_languages() -> None:
    for (kind, side), (vi, en) in FRAMING.items():
        assert vi and en, (kind, side)
        for text in (vi, en):
            assert not any(ch.isdigit() for ch in text), text
            assert "đội nhà" not in text.casefold()  # "đội nhà" means the home side (Day 2)


def test_rival_minor_items_are_low_for_casual_fans_but_never_dropped() -> None:
    fan = Profile(mode="casual", team=HOME)
    rival_corner = item_of("corner", AWAY)
    view, _ = render_commentary(rival_corner, fan, context())
    assert view.relevance == "low" and view.text
    analyst = Profile(mode="analyst", team=HOME)
    assert render_commentary(rival_corner, analyst, context())[0].relevance == "normal"
    assert render_commentary(item_of("goal", AWAY), fan, context())[0].relevance == "high"


def test_club_swap_mirrors_the_point_of_view() -> None:
    """S-9: the same items for a fan of each club: own_team and opponent swap, framings swap."""
    match = context()
    for item in items():
        if item.team is None:
            continue
        home_fan = render_commentary(item, Profile(mode="casual", team=HOME), match)[0]
        away_fan = render_commentary(item, Profile(mode="casual", team=AWAY), match)[0]
        assert {home_fan.emphasis, away_fan.emphasis} == {"own_team", "opponent"}
        if home_fan.framing is not None and item.trigger_kind != "full_time":
            assert away_fan.framing is not None and away_fan.framing != home_fan.framing


def test_full_time_framing_follows_the_result() -> None:
    full_time = item_of("full_time")  # Harbor 1-2 Kestrel
    match = context()
    loser = render_commentary(full_time, Profile(mode="casual", team=HOME), match)[0]
    winner = render_commentary(full_time, Profile(mode="casual", team=AWAY), match)[0]
    assert loser.framing == FRAMING[("full_time", "loss")][0]
    assert winner.framing == FRAMING[("full_time", "win")][0]


# --------------------------------------------------------------------------- beginner rules


def test_a_rule_is_explained_in_full_once_per_viewer() -> None:
    profile = Profile(mode="casual", beginner=True)
    seen: set[RuleId] = set()
    full: list[str] = []
    for item in items():
        view, shown = render_commentary(item, profile, context(), seen=seen)
        if shown is not None:
            seen.add(shown)
        if view.explanation is not None and view.explanation.depth == "full":
            full.append(view.explanation.rule)
    assert len(full) == len(set(full))
    assert {"offside", "corner", "yellow_card", "substitution", "added_time"} <= set(full)


def test_red_cards_are_reminded_in_short_every_time() -> None:
    red = item_of("red_card", scenario="red_card_swing", seed=7)
    profile = Profile(beginner=True)
    view, shown = render_commentary(red, profile, context(), seen={"red_card"})
    assert shown is None and view.explanation is not None
    assert view.explanation.rule == "red_card" and view.explanation.depth == "short"


def test_statistics_rules_wait_for_a_line_that_names_the_metric() -> None:
    """Casual lines never say xG, so a casual beginner is never taught xG under them."""
    casual = Profile(mode="casual", beginner=True)
    analyst = Profile(mode="analyst", beginner=True)
    chance = item_of("big_chance")
    assert "xg" in chance.rules
    casual_view = render_commentary(chance, casual, context(), seen=set())[0]
    assert casual_view.explanation is None or casual_view.explanation.rule != "xg"
    analyst_view = render_commentary(chance, analyst, context(), seen=set())[0]
    assert analyst_view.explanation is not None and analyst_view.explanation.rule == "xg"


def test_rest_rendering_explains_at_the_first_occurrence() -> None:
    profile = Profile(beginner=True)
    explained = [
        item.trigger_kind
        for item in items()
        if (view := render_stateless(item, profile, context())).explanation is not None
        and view.explanation.depth == "full"
    ]
    assert explained.count("offside") == 1


# --------------------------------------------------------------------------- D1-D4 at payload level


def test_analyst_and_casual_payloads_differ() -> None:
    """Two viewers of the same goal get different text, facts and numbers (the two
    experiences the rules ask for)."""
    goal = item_of("goal", AWAY)
    match = context()
    analyst = render_commentary(goal, Profile(mode="analyst", beginner=True), match)[0]
    casual = render_commentary(goal, Profile(mode="casual", beginner=True), match)[0]
    assert analyst.text != casual.text and analyst.facts and not casual.facts

    def count(text: str) -> int:
        return stat_number_count(extract_numbers(Text.of(text)))

    assert count(analyst.text) >= count(casual.text)


# --------------------------------------------------------------------------- player focus, metric


def test_player_focus() -> None:
    goal = item_of("goal", AWAY)
    scorer = goal.player
    assert scorer is not None
    view = render_commentary(goal, Profile(player=scorer), context())[0]
    assert view.emphasis == "focus_player" and view.relevance == "high"
    tick = item_of("stat_tick")
    run = offline(*DL)
    tracked = tracker_facts(run.view_at(tick.as_of_seq), scorer, "vi")
    assert tracked and all(f.display for f in tracked)

    def tracker():  # type: ignore[no-untyped-def]
        return tracker_facts(view_at(*DL, tick.as_of_seq), scorer, "vi")

    tick_view = render_commentary(tick, Profile(player=scorer), context(), tracker=tracker)[0]
    assert tick_view.player_tracker == tracked


def test_one_metric_only() -> None:
    tick = item_of("stat_tick")

    def only_xg():  # type: ignore[no-untyped-def]
        return metric_facts(view_at(*DL, tick.as_of_seq), "xg", "en")

    view = render_commentary(
        tick, Profile(language="en", metric="xg"), context(), metric_facts=only_xg
    )[0]
    assert view.facts and all("xG" in f.label for f in view.facts)


# --------------------------------------------------------------------------- graphics and recaps


def test_lower_third_and_recap_views_follow_the_language() -> None:
    run = offline(*DL)
    lower_third = next(o.lower_third for o in run.outputs if o.lower_third is not None)
    vi = render_lower_third(lower_third, Profile(language="vi"))
    en = render_lower_third(lower_third, Profile(language="en"))
    assert vi.title == lower_third.title_vi and en.title == lower_third.title_en
    assert vi.stats[0].label == lower_third.stats[0].label_vi
    assert vi.synthetic and "Synthetic" in vi.disclaimer
    recap = run.recaps["FULL"][-1]
    for language in ("vi", "en"):
        for mode in ("analyst", "casual"):
            view = render_recap(recap, Profile(language=language, mode=mode))  # type: ignore[arg-type]
            assert view.paragraphs == recap.text(language, mode).paragraphs  # type: ignore[arg-type]
