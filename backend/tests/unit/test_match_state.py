"""MatchState: at least one negative test per invariant, plus atomicity and the snapshot."""

from collections.abc import Callable
from typing import Any

import pytest
from builders import MatchBuilder

from pundit.domain.errors import InvalidEventError
from pundit.domain.events import (
    BallRecoveryEvent,
    CardEvent,
    GoalEvent,
    MatchStartEvent,
    PassEvent,
    PeriodStartEvent,
    PlayerPhysicalEvent,
    PressureEvent,
    ShotEvent,
    SubstitutionEvent,
)
from pundit.domain.xmodels import xg


def kicked_off() -> MatchBuilder:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    return b


def rejects(code: str, b: MatchBuilder, action: Callable[[], Any]) -> None:
    before = (len(b.state), b.state.ball)
    seq = b.factory.seq
    with pytest.raises(InvalidEventError) as error:
        action()
    b.factory.seq = seq  # the factory numbered the rejected event; give the number back
    assert error.value.code == code, error.value
    assert (len(b.state), b.state.ball) == before  # nothing was committed


def open_pass(b: MatchBuilder, ref: str, to: str, **overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "team": b.team_of(ref),
        "player": b.p(ref),
        "x": 44.0,
        "y": 34.0,
        "outcome": "complete",
        "end_x": 54.0,
        "end_y": 34.0,
        "duration_ms": 700,
        "height": "ground",
        "play_type": "open_play",
        "recipient": b.p(to),
        **b.poss(),
    }
    fields.update(overrides)
    return b.emit(PassEvent, **fields)


def test_inv01_sequence_and_match_id() -> None:
    b = kicked_off()
    b.factory.seq += 1  # skip a number
    rejects("INV-01", b, lambda: b.pass_("H8", "H6"))


def test_inv02_phase_order() -> None:
    b = MatchBuilder(start=False)
    rejects("INV-02", b, lambda: b.emit(PeriodStartEvent))
    b.emit(MatchStartEvent)
    rejects("INV-02", b, lambda: b.emit(MatchStartEvent))


def test_inv03_time_cannot_go_backwards() -> None:
    b = kicked_off()
    b.at("0:10").pass_("H8", "H6")
    b.at("0:05")
    rejects("INV-03", b, lambda: b.pass_("H6", "H4"))


def test_inv05_related_events_must_fit() -> None:
    b = kicked_off()
    b.at("0:03").pass_("H8", "H6")
    rejects("INV-05", b, lambda: b.interception("A4"))  # the last pass was complete


def test_inv05_card_must_follow_the_foulers_foul() -> None:
    b = kicked_off()
    b.at("0:03").foul("A4")
    b.ball_dead("foul")
    rejects("INV-05", b, lambda: b.card("A6"))


def test_inv07_pass_speed_must_be_plausible() -> None:
    b = kicked_off()
    rejects("INV-07", b, lambda: b.at("0:03").pass_("H8", "H6", duration_ms=9_000))


def test_inv08_people_must_be_in_the_match() -> None:
    b = kicked_off()
    rejects(
        "INV-08",
        b,
        lambda: b.emit(
            PressureEvent,
            team=b.team_of("A4"),
            player=b.p("A4"),
            x=50.0,
            y=30.0,
            target_player="harbor_lions-77",
            duration_ms=500,
            **b.poss(),
        ),
    )


def test_inv09_bench_players_cannot_play() -> None:
    b = kicked_off()
    rejects("INV-09", b, lambda: b.at("0:03").pass_("H8", "H19"))


def test_inv10_substitutions() -> None:
    b = kicked_off()
    b.at("0:03").pass_("H8", outcome="out", end_x=60.0, end_y=0.0)
    b.at("0:05").ball_dead("out_of_play")
    b.substitution("H7", "H17")
    rejects("INV-10", b, lambda: b.substitution("H11", "H17"))  # already used
    rejects("INV-10", b, lambda: b.substitution("H11", "H8"))  # not on the bench


def test_inv10_at_most_five_substitutions() -> None:
    b = kicked_off()
    b.at("0:03").pass_("H8", outcome="out", end_x=60.0, end_y=0.0)
    b.at("0:05").ball_dead("out_of_play")
    for out_ref, in_ref in (
        ("H2", "H12"),
        ("H4", "H15"),
        ("H6", "H16"),
        ("H8", "H14"),
        ("H7", "H17"),
    ):
        b.substitution(out_ref, in_ref)
    rejects("INV-10", b, lambda: b.substitution("H11", "H18"))


def test_inv11_cards() -> None:
    b = kicked_off()
    b.at("0:03").foul("A4")
    b.ball_dead("foul")
    b.card("A4", "yellow")
    rejects("INV-11", b, lambda: b.card("A4", "yellow"))
    rejects(
        "INV-11",
        b,
        lambda: b.emit(CardEvent, team=b.team_of("A6"), player=b.p("A6"), color="second_yellow"),
    )
    b.card("A4", "second_yellow")
    assert b.p("A4") not in b.state.on_pitch(b.team_of("A4"))
    rejects(
        "INV-09", b, lambda: b.emit(CardEvent, team=b.team_of("A4"), player=b.p("A4"), color="red")
    )


def test_inv11_team_never_below_seven() -> None:
    b = kicked_off()
    b.at("0:03").pass_("H8", outcome="out", end_x=60.0, end_y=0.0)
    b.at("0:05").ball_dead("out_of_play")
    for ref in ("A2", "A3", "A4", "A5"):
        b.card(ref, "red", with_foul=False)
    rejects("INV-11", b, lambda: b.card("A6", "red", with_foul=False))


def test_inv13_possession_team_matches_the_actor() -> None:
    b = kicked_off()
    rejects(
        "INV-13",
        b,
        lambda: b.emit(
            PressureEvent,
            team=b.team_of("A4"),
            player=b.p("A4"),
            x=50.0,
            y=30.0,
            target_player=b.p("H8"),
            duration_ms=500,
            possession_id=1,
            possession_team=b.team_of("A4"),
        ),
    )


def test_inv15_only_the_recipient_can_play_the_ball() -> None:
    b = kicked_off()  # H9 -> H8
    rejects("INV-15", b, lambda: open_pass(b, "H6", "H4"))
    rejects("INV-15", b, lambda: b.pressure("A4", "H6"))


def test_inv16_goal_sequence() -> None:
    b = kicked_off()
    b.at("0:05").pass_("H8", "H9", end_x=94.0, end_y=34.0)
    rejects(  # a goal must reference a scoring shot
        "INV-05",
        b,
        lambda: b.emit(
            GoalEvent,
            team=b.team_of("H9"),
            player=b.p("H9"),
            related_ids=(b.last("pass").id,),
            **b.poss(),
        ),
    )
    b.at("0:06").shot("H9", outcome="saved")
    rejects(  # ...and only a shot that went in
        "INV-05",
        b,
        lambda: b.emit(
            GoalEvent,
            team=b.team_of("H9"),
            player=b.p("H9"),
            related_ids=(b.last("shot").id,),
            **b.poss(),
        ),
    )
    b.at("0:06.500").ball_recovery("A1")
    b.at("0:07").tackle("H9")
    b.at("0:07.200").shot("H9", outcome="goal")
    rejects("INV-16", b, lambda: b.ball_dead("out_of_play"))  # the goal must come first
    b.at("0:07.500").goal()
    rejects("INV-16", b, lambda: b.at("0:09").ball_dead("goal"))  # same moment as the goal
    b.at("0:07.500").ball_dead("goal")
    assert b.state.snapshot().score[b.team_of("H9")] == 1


def test_inv17_restarts_follow_the_stoppage() -> None:
    b = kicked_off()
    b.at("0:03").foul("A4")
    b.ball_dead("foul")
    rejects("INV-17", b, lambda: b.at("0:30").throw_in("H2", "H4"))
    rejects("INV-17", b, lambda: b.at("0:30").free_kick("A4", "A6"))
    b.at("0:30").free_kick("H8", "H6")


def test_inv18_xg_must_come_from_the_model() -> None:
    b = kicked_off()
    b.at("0:05").pass_("H8", "H9", end_x=94.0, end_y=34.0)
    rejects(
        "INV-18",
        b,
        lambda: b.emit(
            ShotEvent,
            team=b.team_of("H9"),
            player=b.p("H9"),
            x=94.0,
            y=34.0,
            outcome="saved",
            xg=0.5,
            body_part="right_foot",
            situation="open_play",
            speed_kmh=90.0,
            **b.poss(),
        ),
    )
    rejects(
        "INV-18",
        b,
        lambda: b.emit(
            ShotEvent,
            team=b.team_of("H9"),
            player=b.p("H9"),
            x=94.0,
            y=34.0,
            outcome="saved",
            xg=xg(94.0, 34.0, "head", "open_play"),
            body_part="head",
            situation="open_play",
            speed_kmh=110.0,
            **b.poss(),
        ),
    )


def test_inv19_var_reviews_the_goal_awaiting_kickoff() -> None:
    b = kicked_off()
    b.at("0:05").pass_("H8", "H9", end_x=94.0, end_y=34.0)
    b.at("0:07").shot("H9", outcome="goal")
    b.goal()
    b.ball_dead("goal")
    b.at("1:30").kick_off("A9", "A8")
    rejects("INV-19", b, lambda: b.var_check())  # play has restarted


def test_var_overturn_voids_the_goal_and_needs_a_free_kick() -> None:
    b = kicked_off()
    b.at("0:05").pass_("H8", "H9", end_x=94.0, end_y=34.0)
    b.at("0:07").shot("H9", outcome="goal")
    b.goal()
    b.ball_dead("goal")
    b.at("1:30").var_check()
    b.offside("H9", via_var=True)
    assert b.state.snapshot().score[b.team_of("H9")] == 0
    rejects("INV-17", b, lambda: b.at("2:00").kick_off("A9", "A8"))
    b.at("2:00").free_kick("A4", "A6", x=15.0, y=30.0)


def test_inv20_physical_values_are_cumulative() -> None:
    b = kicked_off()
    b.at("5:00").physical("H8", 600.0, 30.0, 2)
    rejects("INV-20", b, lambda: b.physical("H8", 500.0, 30.0, 2))
    rejects("INV-20", b, lambda: b.physical("H8", 700.0, 40.0, 3))
    rejects("INV-20", b, lambda: b.physical("H8", 3_000.0, 31.0, 3))  # 3 km in 5 minutes


def test_substitute_takes_part_after_coming_on() -> None:
    b = kicked_off()
    b.at("0:03").pass_("H8", outcome="out", end_x=60.0, end_y=0.0)
    b.at("0:05").ball_dead("out_of_play")
    b.substitution("H8", "H14")
    rejects(
        "INV-09",
        b,
        lambda: b.emit(
            SubstitutionEvent, team=b.team_of("H8"), player=b.p("H8"), player_in=b.p("H18")
        ),
    )
    b.at("0:20").throw_in("H14", "H6")
    assert b.p("H14") in b.state.on_pitch(b.team_of("H14"))


def test_rejected_event_leaves_state_usable() -> None:
    b = kicked_off()
    rejects("INV-09", b, lambda: b.at("0:03").pass_("H8", "H19"))
    b.at("0:03").pass_("H8", "H6")
    assert b.state.ball_carrier == b.p("H6")


def test_loose_ball_recovery_after_a_failed_pass() -> None:
    b = kicked_off()
    b.at("0:05").pass_("H8", outcome="incomplete")
    rejects(  # the passer's own team cannot "recover" its own failed pass as a turnover
        "INV-05",
        b,
        lambda: b.emit(
            BallRecoveryEvent,
            team=b.team_of("H6"),
            player=b.p("H6"),
            x=50.0,
            y=30.0,
            related_ids=(b.last("pass").id,),
            possession_id=2,
            possession_team=b.team_of("H6"),
        ),
    )
    b.emit(
        BallRecoveryEvent,
        team=b.team_of("A4"),
        player=b.p("A4"),
        x=50.0,
        y=30.0,
        related_ids=(b.last("pass").id,),
        possession_id=2,
        possession_team=b.team_of("A4"),
    )
    assert b.state.ball.holder == b.team_of("A4")


def test_physical_event_is_valid_any_time() -> None:
    b = kicked_off()
    b.at("0:03").pass_("H8", outcome="out", end_x=60.0, end_y=0.0)
    b.at("0:05").ball_dead("out_of_play")
    b.emit(
        PlayerPhysicalEvent,
        team=b.team_of("A4"),
        player=b.p("A4"),
        distance_m=10.0,
        top_speed_kmh=20.0,
        sprints=0,
    )


def test_snapshot_reports_live_possession() -> None:
    b = kicked_off()
    b.at("0:10").pass_("H8", outcome="incomplete")
    b.at("0:12").interception("A4")
    b.at("0:20").pass_("A4", "A6")
    snap = b.state.snapshot()
    home, away = b.meta.team_ids
    assert snap.possession_ms == {home: 12_000, away: 8_000}
    assert snap.ball_in_play_ms == 20_000
    assert snap.possession_pct[home] == pytest.approx(60.0)
    assert snap.holder_team == away and snap.ball_in_play
    assert snap.clock_display == "1'"
    assert snap.as_of_ms == 20_000
