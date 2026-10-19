"""Ball state machine: legal transitions and the exact error for illegal ones (INV-14, INV-22)."""

import pytest
from builders import MatchBuilder

from pundit.domain.ballstate import INITIAL_BALL_STATE, step
from pundit.domain.errors import InvalidEventError
from pundit.domain.events import (
    BallDeadEvent,
    InterceptionEvent,
    OffsideEvent,
    PassEvent,
    PeriodEndEvent,
)
from pundit.stats.possession import possession_timeline


def started() -> MatchBuilder:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    return b


def expect_code(code: str, b: MatchBuilder, cls: type, **fields: object) -> None:
    seq = b.factory.seq
    event = b.make(cls, **fields)
    b.factory.seq = seq  # the event is never applied
    with pytest.raises(InvalidEventError) as error:
        step(b.state.ball, event)  # type: ignore[arg-type]
    assert error.value.code == code


def test_initial_state_is_dead() -> None:
    assert INITIAL_BALL_STATE.holder is None
    assert INITIAL_BALL_STATE.possession_id == 0


def test_restart_opens_possession_and_interception_hands_it_over() -> None:
    b = started()
    assert b.state.ball.holder == b.team_of("H9")
    b.at("0:04").pass_("H8", "H6")
    b.at("0:10").pass_("H6", outcome="incomplete")
    b.at("0:12").interception("A4")
    assert b.state.ball.holder == b.team_of("A4")
    assert b.state.ball.possession_id == 2
    [first, current] = possession_timeline(b.view())
    assert (first.start_ms, first.end_ms) == (0, 12_000)
    assert current.end_event_id is None


def test_restart_while_ball_in_play_is_rejected() -> None:
    b = started()
    expect_code(
        "INV-22",
        b,
        PassEvent,
        team=b.team_of("A4"),
        player=b.p("A4"),
        x=30.0,
        y=0.0,
        outcome="complete",
        end_x=40.0,
        end_y=10.0,
        duration_ms=800,
        height="low",
        play_type="throw_in",
        recipient=b.p("A6"),
        possession_id=2,
        possession_team=b.team_of("A4"),
    )


def test_open_play_while_ball_dead_is_rejected() -> None:
    b = started()
    b.at("0:03").pass_("H8", outcome="out", end_x=60.0, end_y=0.0)
    b.at("0:05").ball_dead("out_of_play")
    expect_code(
        "INV-22",
        b,
        PassEvent,
        team=b.team_of("A4"),
        player=b.p("A4"),
        x=40.0,
        y=30.0,
        outcome="complete",
        end_x=50.0,
        end_y=30.0,
        duration_ms=700,
        height="ground",
        play_type="open_play",
        recipient=b.p("A6"),
        possession_id=1,
        possession_team=b.team_of("H9"),
    )


def test_ball_dead_twice_and_period_end_in_play_are_rejected() -> None:
    b = started()
    expect_code("INV-22", b, PeriodEndEvent)
    b.at("0:03").pass_("H8", outcome="out", end_x=60.0, end_y=0.0)
    b.at("0:05").ball_dead("out_of_play")
    expect_code(
        "INV-22",
        b,
        BallDeadEvent,
        team=b.team_of("H9"),
        reason="out_of_play",
        related_ids=(b.last("pass").id,),
        possession_id=1,
        possession_team=b.team_of("H9"),
    )


def test_holder_cannot_win_the_ball_from_itself() -> None:
    b = started()
    b.at("0:03").pass_("H8", outcome="incomplete")
    expect_code(
        "INV-22",
        b,
        InterceptionEvent,
        team=b.team_of("H5"),
        player=b.p("H5"),
        x=40.0,
        y=30.0,
        related_ids=(b.last("pass").id,),
        possession_id=2,
        possession_team=b.team_of("H5"),
    )


def test_possession_ids_must_be_consecutive() -> None:
    b = started()
    b.at("0:03").pass_("H8", outcome="incomplete")
    expect_code(
        "INV-14",
        b,
        InterceptionEvent,
        team=b.team_of("A4"),
        player=b.p("A4"),
        x=40.0,
        y=30.0,
        related_ids=(b.last("pass").id,),
        possession_id=5,
        possession_team=b.team_of("A4"),
    )


def test_stale_possession_is_rejected() -> None:
    b = started()
    expect_code(
        "INV-14",
        b,
        PassEvent,
        team=b.team_of("H8"),
        player=b.p("H8"),
        x=44.0,
        y=34.0,
        outcome="complete",
        end_x=50.0,
        end_y=34.0,
        duration_ms=500,
        height="ground",
        play_type="open_play",
        recipient=b.p("H6"),
        possession_id=7,
        possession_team=b.team_of("H8"),
    )


def test_var_offside_only_while_dead() -> None:
    b = started()
    expect_code(
        "INV-22",
        b,
        OffsideEvent,
        team=b.team_of("H8"),
        player=b.p("H8"),
        x=90.0,
        y=30.0,
        via_var=True,
        related_ids=("a", "b"),
        possession_id=1,
        possession_team=b.team_of("H8"),
    )
