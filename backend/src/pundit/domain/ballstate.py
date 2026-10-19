"""Ball state machine (docs/day1-design.md section 4.4).

The ball is either dead or in play for exactly one team. This module is the single place
that knows how events move the ball between those states; MatchState uses it to validate
and the stats use it to measure possession time.
"""

from dataclasses import dataclass, replace
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from pundit.domain.errors import InvalidEventError
from pundit.domain.events import (
    BallRecoveryEvent,
    Event,
    InterceptionEvent,
    OffsideEvent,
    PassEvent,
    TackleEvent,
)
from pundit.domain.ids import TeamId

ANY_STATE_TYPES = frozenset({"card", "substitution", "var_check", "player_physical"})
DEAD_ONLY_TYPES = frozenset({"match_start", "period_start", "period_end", "match_end"})


class PossessionInterval(BaseModel):
    """A span of played time during which one team had the ball."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    team: TeamId
    possession_id: int
    period: Literal[1, 2]
    period_start_ms: int
    start_ms: int
    end_ms: int
    start_event_id: str
    end_event_id: str | None  # None = still running (live view)

    @model_validator(mode="after")
    def _check_interval(self) -> Self:
        if self.end_ms < self.start_ms:
            raise ValueError("interval ends before it starts")
        return self

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True, slots=True)
class BallState:
    period: Literal[1, 2] | None = None
    period_start_ms: int = 0
    holder: str | None = None  # None = ball dead
    possession_id: int = 0
    last_team: str | None = None  # team of the current or most recent possession
    since_ms: int = 0
    start_event_id: str | None = None


INITIAL_BALL_STATE = BallState()


def opens_possession(event: Event) -> bool:
    """Restart passes and ball-winning events start a new possession."""
    if isinstance(event, PassEvent):
        return event.play_type != "open_play"
    if isinstance(event, TackleEvent):
        return event.outcome == "won"
    return isinstance(event, InterceptionEvent | BallRecoveryEvent)


def _open(state: BallState, event: Event) -> BallState:
    if event.possession_id != state.possession_id + 1:
        raise InvalidEventError(
            "INV-14",
            event.id,
            f"new possession must have id {state.possession_id + 1}, got {event.possession_id}",
        )
    if event.possession_team != event.team:
        raise InvalidEventError("INV-14", event.id, "possession_team must be the team taking over")
    assert event.team is not None and state.period is not None
    return BallState(
        period=state.period,
        period_start_ms=state.period_start_ms,
        holder=event.team,
        possession_id=state.possession_id + 1,
        last_team=event.team,
        since_ms=event.match_ms,
        start_event_id=event.id,
    )


def _close(state: BallState, event: Event) -> PossessionInterval:
    assert state.holder is not None and state.start_event_id is not None
    assert state.period in (1, 2)
    return PossessionInterval(
        team=state.holder,
        possession_id=state.possession_id,
        period=state.period,
        period_start_ms=state.period_start_ms,
        start_ms=state.since_ms,
        end_ms=event.match_ms,
        start_event_id=state.start_event_id,
        end_event_id=event.id,
    )


def step(state: BallState, event: Event) -> tuple[BallState, PossessionInterval | None]:
    """Apply one event. Returns the new state and the interval it closed, if any.

    Raises InvalidEventError (INV-22 for ball-state rules, INV-14 for possession ids).
    """
    kind = event.type
    if kind in ANY_STATE_TYPES:
        return state, None

    if kind in DEAD_ONLY_TYPES:
        if state.holder is not None:
            raise InvalidEventError("INV-22", event.id, f"{kind} while the ball is in play")
        if kind == "period_start":
            return replace(state, period=event.period, period_start_ms=event.match_ms), None
        return state, None

    if isinstance(event, PassEvent) and event.play_type != "open_play":
        if state.holder is not None:
            raise InvalidEventError("INV-22", event.id, "restart while the ball is in play")
        if state.period is None:
            raise InvalidEventError("INV-22", event.id, "restart outside a period")
        return _open(state, event), None

    if isinstance(event, OffsideEvent) and event.via_var:
        if state.holder is not None:
            raise InvalidEventError("INV-22", event.id, "VAR offside while the ball is in play")
        if event.possession_id != state.possession_id or event.possession_team != state.last_team:
            raise InvalidEventError(
                "INV-14", event.id, "VAR offside must reference the last possession"
            )
        return state, None

    if state.holder is None:
        raise InvalidEventError("INV-22", event.id, f"{kind} while the ball is dead")

    if opens_possession(event):
        if event.team == state.holder:
            raise InvalidEventError(
                "INV-22", event.id, "ball-winning event by the team already in possession"
            )
        return _open(state, event), _close(state, event)

    if event.possession_id != state.possession_id or event.possession_team != state.holder:
        raise InvalidEventError(
            "INV-14", event.id, "event does not belong to the current possession"
        )
    if kind == "ball_dead":
        closed = _close(state, event)
        return replace(state, holder=None, since_ms=event.match_ms, start_event_id=None), closed
    return state, None
