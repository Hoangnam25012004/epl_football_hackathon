"""Event schema v1.0 (docs/day1-design.md section 4).

Events are immutable. `type` is the discriminator. Fields that only make sense for some
types (team, player, location, possession) are required or forbidden per type, so an
event with a missing or stray field cannot be constructed.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from pundit.domain.ids import (
    EVENT_ID_PATTERN,
    MATCH_ID_PATTERN,
    MAX_SEQ,
    PlayerId,
    TeamId,
    event_id,
    team_of_player,
)
from pundit.domain.matchtime import minute_fields
from pundit.domain.pitch import PITCH_LENGTH, PITCH_WIDTH
from pundit.domain.types import (
    BodyPart,
    CardColor,
    CheckType,
    DeadReason,
    Height,
    PassOutcome,
    PlayType,
    ShotOutcome,
    Situation,
    TackleOutcome,
    VarDecision,
    VarReason,
)
from pundit.domain.xmodels import PENALTY_XG

XCoord = Annotated[float, Field(ge=0.0, le=PITCH_LENGTH)]
YCoord = Annotated[float, Field(ge=0.0, le=PITCH_WIDTH)]

# type -> (team, player, location, possession): True = required, False = forbidden
FIELD_RULES: dict[str, tuple[bool, bool, bool, bool]] = {
    "match_start": (False, False, False, False),
    "period_start": (False, False, False, False),
    "period_end": (False, False, False, False),
    "match_end": (False, False, False, False),
    "pass": (True, True, True, True),
    "shot": (True, True, True, True),
    "goal": (True, True, False, True),
    "pressure": (True, True, True, True),
    "tackle": (True, True, True, True),
    "interception": (True, True, True, True),
    "ball_recovery": (True, True, True, True),
    "foul": (True, True, True, True),
    "card": (True, True, False, False),
    "offside": (True, True, True, True),
    "ball_dead": (True, False, False, True),
    "substitution": (True, True, False, False),
    "var_check": (False, False, False, False),
    "player_physical": (True, True, False, False),
}
EVENT_TYPES: tuple[str, ...] = tuple(FIELD_RULES)


def _require(rule: bool, present: bool, name: str, event_type: str) -> None:
    if rule and not present:
        raise ValueError(f"{name} is required for {event_type}")
    if not rule and present:
        raise ValueError(f"{name} is not allowed for {event_type}")


class EventBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str
    id: str = Field(pattern=EVENT_ID_PATTERN)
    match_id: str = Field(pattern=MATCH_ID_PATTERN)
    seq: int = Field(ge=0, le=MAX_SEQ)
    period: Literal[1, 2]
    clock_ms: int = Field(ge=0, le=3_600_000)
    match_ms: int = Field(ge=0)
    minute: int = Field(ge=0)
    second: int = Field(ge=0, le=59)
    team: TeamId | None = None
    player: PlayerId | None = None
    x: XCoord | None = None
    y: YCoord | None = None
    related_ids: tuple[str, ...] = ()
    possession_id: int | None = Field(default=None, ge=1)
    possession_team: TeamId | None = None

    @model_validator(mode="after")
    def _check_base(self) -> Self:
        if self.id != event_id(self.match_id, self.seq):
            raise ValueError(f"id {self.id!r} does not match match_id {self.match_id!r} / seq")
        if (self.minute, self.second) != minute_fields(self.period, self.clock_ms):
            raise ValueError("minute/second inconsistent with period/clock_ms")
        team_rule, player_rule, location_rule, possession_rule = FIELD_RULES[self.type]
        _require(team_rule, self.team is not None, "team", self.type)
        _require(player_rule, self.player is not None, "player", self.type)
        if (self.x is None) != (self.y is None):
            raise ValueError("x and y must be set together")
        _require(location_rule, self.x is not None, "x/y", self.type)
        if (self.possession_id is None) != (self.possession_team is None):
            raise ValueError("possession_id and possession_team must be set together")
        _require(possession_rule, self.possession_id is not None, "possession", self.type)
        if (
            self.player is not None
            and self.team is not None
            and team_of_player(self.player) != self.team
        ):
            raise ValueError(f"player {self.player} does not belong to team {self.team}")
        if len(set(self.related_ids)) != len(self.related_ids):
            raise ValueError("related_ids contains duplicates")
        return self

    def _check_opponent(self, other: str, name: str) -> None:
        if team_of_player(other) == self.team:
            raise ValueError(f"{name} must belong to the opponent")

    def _check_teammate(self, other: str, name: str) -> None:
        if team_of_player(other) != self.team:
            raise ValueError(f"{name} must belong to the same team")
        if other == self.player:
            raise ValueError(f"{name} must differ from player")

    def _check_related_count(self, allowed: tuple[int, ...]) -> None:
        if len(self.related_ids) not in allowed:
            raise ValueError(
                f"{self.type} needs {allowed} related_ids, got {len(self.related_ids)}"
            )


class MatchStartEvent(EventBase):
    type: Literal["match_start"] = "match_start"


class PeriodStartEvent(EventBase):
    type: Literal["period_start"] = "period_start"


class PeriodEndEvent(EventBase):
    type: Literal["period_end"] = "period_end"


class MatchEndEvent(EventBase):
    type: Literal["match_end"] = "match_end"


class PassEvent(EventBase):
    type: Literal["pass"] = "pass"
    outcome: PassOutcome
    end_x: XCoord
    end_y: YCoord
    duration_ms: int = Field(gt=0, le=10_000)
    height: Height
    play_type: PlayType
    recipient: PlayerId | None = None
    under_pressure: bool = False
    is_cross: bool = False
    is_through_ball: bool = False

    @model_validator(mode="after")
    def _check_pass(self) -> Self:
        if self.outcome == "complete":
            if self.recipient is None:
                raise ValueError("a complete pass needs a recipient")
            self._check_teammate(self.recipient, "recipient")
        elif self.recipient is not None:
            raise ValueError("only a complete pass has a recipient")
        self._check_related_count((0,))
        return self

    @property
    def is_restart(self) -> bool:
        return self.play_type != "open_play"


class ShotEvent(EventBase):
    type: Literal["shot"] = "shot"
    outcome: ShotOutcome
    xg: float = Field(ge=0.01, le=0.95)
    body_part: BodyPart
    situation: Situation
    speed_kmh: float = Field(ge=20.0, le=140.0)
    goalkeeper: PlayerId | None = None

    @model_validator(mode="after")
    def _check_shot(self) -> Self:
        if self.situation == "penalty" and self.xg != PENALTY_XG:
            raise ValueError(f"penalty xg must be {PENALTY_XG}")
        if self.goalkeeper is not None:
            self._check_opponent(self.goalkeeper, "goalkeeper")
        self._check_related_count((0,))
        return self


class GoalEvent(EventBase):
    type: Literal["goal"] = "goal"
    assist: PlayerId | None = None
    own_goal: bool = False

    @model_validator(mode="after")
    def _check_goal(self) -> Self:
        if self.own_goal:
            raise ValueError("own goals are not supported in schema v1.0")
        if self.assist is not None:
            self._check_teammate(self.assist, "assist")
        self._check_related_count((1,))
        return self


class PressureEvent(EventBase):
    type: Literal["pressure"] = "pressure"
    target_player: PlayerId
    duration_ms: int = Field(gt=0, le=5_000)

    @model_validator(mode="after")
    def _check_pressure(self) -> Self:
        self._check_opponent(self.target_player, "target_player")
        self._check_related_count((0,))
        return self


class TackleEvent(EventBase):
    type: Literal["tackle"] = "tackle"
    outcome: TackleOutcome
    target_player: PlayerId

    @model_validator(mode="after")
    def _check_tackle(self) -> Self:
        self._check_opponent(self.target_player, "target_player")
        self._check_related_count((0,))
        return self


class InterceptionEvent(EventBase):
    type: Literal["interception"] = "interception"

    @model_validator(mode="after")
    def _check_interception(self) -> Self:
        self._check_related_count((1,))
        return self


class BallRecoveryEvent(EventBase):
    type: Literal["ball_recovery"] = "ball_recovery"

    @model_validator(mode="after")
    def _check_recovery(self) -> Self:
        self._check_related_count((0, 1))
        return self


class FoulEvent(EventBase):
    type: Literal["foul"] = "foul"
    fouled_player: PlayerId

    @model_validator(mode="after")
    def _check_foul(self) -> Self:
        self._check_opponent(self.fouled_player, "fouled_player")
        self._check_related_count((0,))
        return self


class CardEvent(EventBase):
    type: Literal["card"] = "card"
    color: CardColor

    @model_validator(mode="after")
    def _check_card(self) -> Self:
        self._check_related_count((0, 1))
        return self


class OffsideEvent(EventBase):
    type: Literal["offside"] = "offside"
    via_var: bool = False

    @model_validator(mode="after")
    def _check_offside(self) -> Self:
        self._check_related_count((2,) if self.via_var else (1,))
        return self


class BallDeadEvent(EventBase):
    type: Literal["ball_dead"] = "ball_dead"
    reason: DeadReason

    @model_validator(mode="after")
    def _check_dead(self) -> Self:
        self._check_related_count((0,) if self.reason == "period_end" else (1,))
        return self


class SubstitutionEvent(EventBase):
    type: Literal["substitution"] = "substitution"
    player_in: PlayerId

    @model_validator(mode="after")
    def _check_sub(self) -> Self:
        self._check_teammate(self.player_in, "player_in")
        self._check_related_count((0,))
        return self


class VarCheckEvent(EventBase):
    type: Literal["var_check"] = "var_check"
    check_type: CheckType
    decision: VarDecision
    reason: VarReason
    duration_ms: int = Field(gt=0, le=600_000)

    @model_validator(mode="after")
    def _check_var(self) -> Self:
        if self.decision == "overturned" and self.reason == "none":
            raise ValueError("an overturned decision needs a reason")
        self._check_related_count((1,))
        return self


class PlayerPhysicalEvent(EventBase):
    type: Literal["player_physical"] = "player_physical"
    distance_m: float = Field(ge=0.0, le=20_000.0)
    top_speed_kmh: float = Field(ge=0.0, le=45.0)
    sprints: int = Field(ge=0, le=500)

    @model_validator(mode="after")
    def _check_physical(self) -> Self:
        self._check_related_count((0,))
        return self


Event = Annotated[
    MatchStartEvent
    | PeriodStartEvent
    | PeriodEndEvent
    | MatchEndEvent
    | PassEvent
    | ShotEvent
    | GoalEvent
    | PressureEvent
    | TackleEvent
    | InterceptionEvent
    | BallRecoveryEvent
    | FoulEvent
    | CardEvent
    | OffsideEvent
    | BallDeadEvent
    | SubstitutionEvent
    | VarCheckEvent
    | PlayerPhysicalEvent,
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)

EVENT_CLASSES: dict[str, type[EventBase]] = {
    "match_start": MatchStartEvent,
    "period_start": PeriodStartEvent,
    "period_end": PeriodEndEvent,
    "match_end": MatchEndEvent,
    "pass": PassEvent,
    "shot": ShotEvent,
    "goal": GoalEvent,
    "pressure": PressureEvent,
    "tackle": TackleEvent,
    "interception": InterceptionEvent,
    "ball_recovery": BallRecoveryEvent,
    "foul": FoulEvent,
    "card": CardEvent,
    "offside": OffsideEvent,
    "ball_dead": BallDeadEvent,
    "substitution": SubstitutionEvent,
    "var_check": VarCheckEvent,
    "player_physical": PlayerPhysicalEvent,
}
