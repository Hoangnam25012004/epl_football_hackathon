"""MomentDetector (design section 10): rules on hand-built data, scenario stories, and batch
detection equal to live detection."""

import itertools

import pytest
from builders import MatchBuilder
from helpers import SCENARIO_KEYS, generated

from pundit.agents.moments import (
    MomentDetector,
    detect_all,
    moment_id,
)
from pundit.state.match_state import MatchState


def feed_all(b: MatchBuilder) -> list:  # type: ignore[type-arg]
    return detect_all(b.meta, b.events)


def test_goal_and_var_overturn() -> None:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("0:04").pass_("H8", "H9", end_x=97.0, end_y=34.0)
    shot = b.at("0:06").shot("H9", outcome="goal", x=97.0, y=34.0)
    goal = b.at("0:06.400").goal(assist="H8")
    b.ball_dead("goal")
    var = b.at("1:30").var_check("overturned", "offside")
    moments = feed_all(b)
    kinds = [m.kind for m in moments]
    assert kinds.count("goal") == 1 and kinds.count("goal_overturned") == 1
    scored = next(m for m in moments if m.kind == "goal")
    assert scored.trigger_event_ids == (shot.id, goal.id)
    assert scored.importance == 3 and scored.player == b.p("H9") and scored.team == b.team_of("H9")
    assert [f.display for f in scored.facts[:2]] == ["1", "0"]  # home, away after the goal
    assert scored.facts[2].key == f"shot_xg:{shot.id}"
    overturned = next(m for m in moments if m.kind == "goal_overturned")
    assert overturned.related_moment_ids == (scored.id,)
    assert set(overturned.trigger_event_ids) == {var.id, goal.id}
    assert [f.display for f in overturned.facts] == ["0", "0"]


def test_cards_shots_and_substitutions() -> None:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("0:05").foul("A4")
    b.ball_dead("foul")
    b.at("0:10").card("A4", "yellow")
    b.at("0:30").free_kick("H8", "H9", x=80.0, y=34.0)
    b.at("0:33").shot("H9", outcome="saved", x=99.0, y=34.0, speed_kmh=112.0)  # big + fast
    b.at("0:35").ball_recovery("A1", x=3.0, y=34.0)
    b.at("0:40").foul("H4", "A1")
    b.ball_dead("foul")
    b.at("0:45").card("H4", "red")
    b.at("1:00").substitution("A9", "A17")
    kinds = [m.kind for m in feed_all(b)]
    assert kinds.count("yellow_card") == 1 and kinds.count("red_card") == 1
    assert "big_chance" in kinds and "rocket_shot" in kinds and "substitution" in kinds
    red = next(m for m in feed_all(b) if m.kind == "red_card")
    assert red.importance == 3 and red.facts[0].metric == "red_cards"


def test_dangerous_spell_fires_once_per_spell() -> None:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("0:04").pass_("H8", "H9", end_x=95.0, end_y=34.0)
    b.at("0:06").shot("H9", outcome="blocked", x=95.0, y=34.0)
    b.at("0:08").pass_("H6", "H7", x=85.0, y=30.0, end_x=88.0, end_y=28.0)
    b.at("0:10").shot("H7", outcome="off_target", x=88.0, y=28.0)
    b.at("0:12").pass_("H4", "H11", x=80.0, y=30.0, end_x=90.0, end_y=40.0)
    b.at("0:14").shot("H11", outcome="saved", x=90.0, y=40.0)
    spells = [m for m in feed_all(b) if m.kind == "dangerous_spell"]
    assert len(spells) == 1
    (spell,) = spells
    assert spell.team == b.team_of("H9") and spell.facts[0].value == 2


def test_ids_are_sequential() -> None:
    match = generated("dominant_loser", 42)
    moments = detect_all(match.meta, match.events)
    assert [m.id for m in moments] == [
        moment_id(match.meta.match_id, n) for n in range(1, len(moments) + 1)
    ]
    assert all(m.seq == match.events[m.seq].seq for m in moments)
    assert [m.seq for m in moments] == sorted(m.seq for m in moments)


def test_dominant_loser_story() -> None:
    match = generated("dominant_loser", 42)
    moments = detect_all(match.meta, match.events)
    goals = [m for m in moments if m.kind == "goal"]
    assert [m.display_minute for m in goals] == ["27'", "63'", "78'"]
    assert [m.team for m in goals] == ["kestrel_athletic", "harbor_lions", "kestrel_athletic"]
    full_time = next(m for m in moments if m.kind == "full_time")
    assert [f.display for f in full_time.facts] == ["1", "2"]
    assert any(m.kind == "momentum_shift" and m.team == "harbor_lions" for m in moments)


def test_comeback_var_overturn_points_at_the_goal() -> None:
    match = generated("comeback_var", 7)
    moments = detect_all(match.meta, match.events)
    (overturned,) = [m for m in moments if m.kind == "goal_overturned"]
    goal_moment = next(m for m in moments if m.id == overturned.related_moment_ids[0])
    assert goal_moment.kind == "goal" and goal_moment.team == overturned.team
    assert set(goal_moment.trigger_event_ids) & set(overturned.trigger_event_ids)
    assert overturned.display_minute in ("35'", "36'", "37'")


def test_red_card_swing() -> None:
    match = generated("red_card_swing", 7)
    reds = [m for m in detect_all(match.meta, match.events) if m.kind == "red_card"]
    assert [(m.display_minute, m.team) for m in reds] == [("52'", "kestrel_athletic")]


def test_cooldowns() -> None:
    for scenario in SCENARIO_KEYS:
        match = generated(scenario, 1)
        moments = detect_all(match.meta, match.events)
        shifts = [match.events[m.seq].match_ms for m in moments if m.kind == "momentum_shift"]
        assert all(b - a >= 5 * 60_000 for a, b in itertools.pairwise(shifts))
        for team in match.meta.team_ids:
            spells = [
                match.events[m.seq].match_ms
                for m in moments
                if m.kind == "dangerous_spell" and m.team == team
            ]
            assert all(b - a >= 3 * 60_000 for a, b in itertools.pairwise(spells))


@pytest.mark.parametrize("scenario", SCENARIO_KEYS)
@pytest.mark.parametrize("seed", [0, 3])
def test_batch_equals_live(scenario: str, seed: int) -> None:
    """The live path (MatchState.apply then feed) gives exactly the batch moments."""
    match = generated(scenario, seed)
    state = MatchState(match.meta)
    detector = MomentDetector(match.meta)
    live = []
    for event in match.events:
        state.apply(event)
        live += detector.feed(event, state.view())
    assert live == detect_all(match.meta, match.events)


def test_every_moment_fact_has_evidence() -> None:
    match = generated("comeback_var", 7)
    for moment in detect_all(match.meta, match.events):
        for fact in moment.facts:
            assert fact.evidence_event_ids, (moment.kind, fact.key)
