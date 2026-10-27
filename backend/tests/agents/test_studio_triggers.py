"""CueDetector and triggers (docs/day3-design.md sections 4 and 9.4)."""

import itertools

import pytest
from builders import MatchBuilder
from helpers import SCENARIO_KEYS, generated
from studio_helpers import MATRIX, SAMPLES, triggers

from pundit.agents.cues import PLAY_GAP_MS, CueDetector, cue_id, detect_cues
from pundit.agents.moments import MomentDetector, detect_all
from pundit.agents.triggers import TriggerFeed, detect_triggers
from pundit.domain.events import (
    FoulEvent,
    OffsideEvent,
    PassEvent,
    ShotEvent,
    TackleEvent,
    VarCheckEvent,
)
from pundit.domain.log import EventLog
from pundit.domain.matchtime import minute_exact
from pundit.stats.team import is_box_entry


def built_triggers(b: MatchBuilder) -> list:  # type: ignore[type-arg]
    return detect_triggers(b.meta, b.events)


# --------------------------------------------------------------------------- cues on built data


def test_a_saved_shot_is_a_cue_with_roles_and_outcome() -> None:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("0:04").pass_("H8", "H9", end_x=80.0, end_y=30.0)
    shot = b.at("0:06").shot("H9", outcome="saved", x=80.0, y=30.0)
    (trigger,) = [t for t in built_triggers(b) if t.kind == "shot"]
    assert trigger.source == "cue" and trigger.importance == 1
    assert trigger.outcome == "saved" and trigger.trigger_event_ids == (shot.id,)
    assert trigger.roles["shooter"] == b.p("H9")  # + "goalkeeper" when the shot names one
    assert {f.key.split(":")[0] for f in trigger.facts} == {"shot_xg", "shot_speed"}
    assert "penalty" not in trigger.rules


def test_shots_that_are_moments_or_goals_are_not_cues() -> None:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("0:04").pass_("H8", "H9", end_x=99.0, end_y=34.0)
    b.at("0:06").shot("H9", outcome="saved", x=99.0, y=34.0, speed_kmh=115.0)  # big + fast
    b.at("0:20").pass_("H8", "H9", end_x=97.0, end_y=34.0)
    b.at("0:22").shot("H9", outcome="goal", x=97.0, y=34.0)
    b.at("0:22.400").goal()
    kinds = [t.kind for t in built_triggers(b)]
    assert "shot" not in kinds
    assert {"big_chance", "goal"} <= set(kinds)


def test_penalty_rules_on_built_data() -> None:
    """The four sample matches have no penalty: the rule is tested on built data (4.3)."""

    def penalty(outcome: str) -> MatchBuilder:
        b = MatchBuilder()
        b.at("0:00").kick_off("H9", "H8")
        b.at("0:28").pass_("H8", "H9", end_x=94.0, end_y=34.0)
        b.at("0:30").shot("H9", outcome=outcome, x=94.0, y=34.0, situation="penalty")
        return b

    missed = penalty("off_target")  # a penalty is a big chance (xG about 0.76)
    shot_id = missed.last("shot").id
    (chance,) = [t for t in built_triggers(missed) if shot_id in t.trigger_event_ids]
    assert chance.kind == "big_chance" and chance.outcome == "off_target"
    assert {"penalty", "xg"} <= set(chance.rules) and chance.details["situation"] == "penalty"
    scored = penalty("goal")
    scored.at("0:30.400").goal()
    goal = next(t for t in built_triggers(scored) if t.kind == "goal")
    assert "penalty" in goal.rules and goal.outcome == "goal"


def test_second_yellow_and_var_stands_on_built_data() -> None:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("0:05").foul("A4")
    b.ball_dead("foul")
    b.at("0:10").card("A4", "yellow")
    b.at("0:20").free_kick("H8", "H6", x=40.0, y=34.0)
    b.at("0:25").foul("A4", "H6")
    b.ball_dead("foul")
    b.at("0:30").card("A4", "second_yellow")
    b.at("0:50").free_kick("H8", "H9", x=80.0, y=34.0)
    b.at("1:02").shot("H9", outcome="goal", x=97.0, y=34.0)
    b.at("1:02.400").goal()
    b.ball_dead("goal")
    var = b.at("2:00").var_check("stands", "offside")
    found = built_triggers(b)
    red = next(t for t in found if t.kind == "red_card")
    assert red.outcome == "second_yellow" and "second_yellow" in red.rules
    review = next(t for t in found if t.kind == "var_review")
    assert review.outcome == "stands" and "var" in review.rules
    assert review.trigger_event_ids == (var.id,)
    assert not any(t.kind == "goal_overturned" for t in found)


def test_an_overturned_goal_is_a_moment_not_a_var_cue() -> None:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("0:04").pass_("H8", "H9", end_x=97.0, end_y=34.0)
    b.at("0:06").shot("H9", outcome="goal", x=97.0, y=34.0)
    b.at("0:06.400").goal(assist="H8")
    b.ball_dead("goal")
    b.at("1:30").var_check("overturned", "offside")
    found = built_triggers(b)
    assert not any(t.kind == "var_review" for t in found)
    overturned = next(t for t in found if t.kind == "goal_overturned")
    assert overturned.outcome == "overturned"
    assert {"var", "offside"} <= set(overturned.rules)
    assert overturned.related_ids  # points at the goal moment


def test_corner_and_offside_cues_count_for_the_team() -> None:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("0:06").pass_("H8", None, outcome="out", end_x=105.0, end_y=5.0)
    b.ball_dead("out_of_play")
    b.at("0:10").corner("H7", "H9")
    b.at("0:40").pass_("H9", None, outcome="offside", end_x=90.0, end_y=34.0)
    b.at("0:41").offside("H10")
    found = built_triggers(b)
    corner = next(t for t in found if t.kind == "corner")
    assert corner.facts[0].metric == "corners" and corner.facts[0].value == 1
    assert corner.rules == ("corner",)
    offside = next(t for t in found if t.kind == "offside")
    assert offside.facts[0].metric == "offsides" and "offside" in offside.rules
    assert offside.roles["offside_player"] == b.p("H10")


# --------------------------------------------------------------------------- generated matches


@pytest.mark.parametrize("scenario", SCENARIO_KEYS)
def test_batch_equals_stream_and_ids_are_sequential(scenario: str) -> None:
    match = generated(scenario, 1)
    moments, cues = MomentDetector(match.meta), CueDetector(match.meta)
    log = EventLog(match.meta)
    streamed = []
    for event in match.events:
        log.append(event)
        view = log.view()
        streamed += cues.feed(event, view, moments.feed(event, view))
    assert streamed == detect_cues(match.meta, match.events)
    assert [c.id for c in streamed] == [
        cue_id(match.meta.match_id, n) for n in range(1, len(streamed) + 1)
    ]
    feed = TriggerFeed(match.meta)
    log = EventLog(match.meta)
    live = []
    for event in match.events:
        log.append(event)
        live += feed.feed(event, log.view())[1]
    assert live == detect_triggers(match.meta, match.events)
    assert len({t.id for t in live}) == len(live)


@pytest.mark.parametrize(("scenario", "seed"), SAMPLES)
def test_cue_counts_follow_the_events(scenario: str, seed: int) -> None:
    match = generated(scenario, seed)
    cues = detect_cues(match.meta, match.events)
    kinds = [c.kind for c in cues]
    assert kinds.count("stat_tick") == 16 and kinds.count("added_time") == 2  # every 5'
    corners = [e for e in match.events if isinstance(e, PassEvent) and e.play_type == "corner"]
    offsides = [e for e in match.events if isinstance(e, OffsideEvent)]
    assert kinds.count("corner") == len(corners)
    assert kinds.count("offside") == len(offsides)
    moment_shots = {
        eid
        for m in detect_all(match.meta, match.events)
        if m.kind in ("big_chance", "rocket_shot")
        for eid in m.trigger_event_ids
    }
    shots = [
        e
        for e in match.events
        if isinstance(e, ShotEvent) and e.outcome != "goal" and e.id not in moment_shots
    ]
    assert kinds.count("shot") == len(shots)
    var_checks = [e for e in match.events if isinstance(e, VarCheckEvent)]
    overturned = [e for e in var_checks if e.check_type == "goal" and e.decision == "overturned"]
    assert kinds.count("var_review") == len(var_checks) - len(overturned)


@pytest.mark.parametrize(("scenario", "seed"), SAMPLES)
def test_small_plays_fill_the_gaps(scenario: str, seed: int) -> None:
    """28/10: a won tackle, a foul or a pass into the box, 40 s after the previous line at least,
    with its team's count so far."""
    match = generated(scenario, seed)
    found = detect_triggers(match.meta, match.events)
    by_id = {e.id: e for e in match.events}
    small = [t for t in found if t.kind in ("tackle", "foul", "box_entry")]
    assert len(small) >= 20, len(small)
    metric = {"tackle": "tackles", "foul": "fouls", "box_entry": "box_entries"}
    for trigger in small:
        event = by_id[trigger.trigger_event_ids[0]]
        assert event.type == {"tackle": "tackle", "foul": "foul", "box_entry": "pass"}[trigger.kind]
        if isinstance(event, TackleEvent):
            assert event.outcome == "won" and trigger.roles["tackled"] == event.target_player
        elif isinstance(event, FoulEvent):
            assert trigger.roles["fouled"] == event.fouled_player
        else:
            assert is_box_entry(event) and trigger.roles["receiver"] == event.recipient
        (fact,) = trigger.facts
        assert fact.metric == metric[trigger.kind] and fact.team == trigger.team
    for previous, current in itertools.pairwise(found):
        if current.kind in metric and previous.period == current.period:
            assert current.match_ms - previous.match_ms >= PLAY_GAP_MS, (previous.id, current.id)


@pytest.mark.parametrize(("scenario", "seed"), SAMPLES)
def test_no_silence_longer_than_three_minutes(scenario: str, seed: int) -> None:
    """S-10: a lull cue keeps the commentary going (every trigger becomes a commentary); 28/10:
    within 3 minutes (lulls after 1.75', on the next event), was 5."""
    found = triggers(scenario, seed)
    for previous, current in itertools.pairwise(found):
        if previous.period == current.period:
            assert current.clock_ms - previous.clock_ms <= 3 * 60_000, (previous.id, current.id)


@pytest.mark.slow
@pytest.mark.parametrize(("scenario", "seed"), MATRIX)
def test_no_long_silence_on_the_matrix(scenario: str, seed: int) -> None:
    test_no_silence_longer_than_three_minutes(scenario, seed)


def test_stat_ticks_are_the_first_events_after_each_mark() -> None:
    match = generated("dominant_loser", 42)
    ticks = [t for t in triggers("dominant_loser", 42) if t.kind == "stat_tick"]
    assert [t.details["tick_minute"] for t in ticks] == [
        f"{m}" for m in (*range(5, 45, 5), *range(50, 90, 5))
    ]
    for tick in ticks:
        mark = float(tick.details["tick_minute"])
        event = match.events[tick.seq]
        assert minute_exact(event.period, event.clock_ms) >= mark
        before = match.events[tick.seq - 1]
        assert before.period != event.period or minute_exact(before.period, before.clock_ms) < mark


# --------------------------------------------------------------------------- rules (table 9.4)


EXPECTED = {
    "dominant_loser-42": (set(), {"var", "penalty", "red_card", "second_yellow"}),
    "comeback_var-7": ({"var"}, {"penalty", "red_card"}),
    "red_card_swing-7": ({"red_card"}, {"var", "penalty", "second_yellow"}),
    "balanced-3": (set(), {"var", "penalty", "red_card"}),
}
BASE = {"offside", "corner", "yellow_card", "substitution", "added_time", "xg", "possession"}


@pytest.mark.parametrize(("scenario", "seed"), SAMPLES)
def test_trigger_rules_follow_table_9_4(scenario: str, seed: int) -> None:
    rules = {rule for t in triggers(scenario, seed) for rule in t.rules}
    extra, absent = EXPECTED[f"{scenario}-{seed}"]
    assert BASE | extra | {"momentum"} <= rules
    assert not rules & absent


def test_red_card_and_var_minutes_of_the_samples() -> None:
    red = next(t for t in triggers("red_card_swing", 7) if t.kind == "red_card")
    assert red.display_minute == "52'" and red.outcome == "red"
    var = next(t for t in triggers("comeback_var", 7) if t.kind == "goal_overturned")
    assert var.display_minute == "36'" and "var" in var.rules
