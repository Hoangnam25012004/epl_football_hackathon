"""MatchState: the single entry point for events (docs/day1-design.md sections 6.2 and 10).

`apply()` checks every invariant first and only then commits, so a rejected event leaves the
state untouched. Statistics are never stored here; the snapshot asks the stats module.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from pundit.domain.ballstate import INITIAL_BALL_STATE, BallState, opens_possession, step
from pundit.domain.errors import InvalidEventError, UnknownPlayerError, UnknownTeamError
from pundit.domain.events import (
    BallDeadEvent,
    BallRecoveryEvent,
    CardEvent,
    Event,
    FoulEvent,
    GoalEvent,
    InterceptionEvent,
    OffsideEvent,
    PassEvent,
    PlayerPhysicalEvent,
    PressureEvent,
    ShotEvent,
    SubstitutionEvent,
    TackleEvent,
    VarCheckEvent,
)
from pundit.domain.log import EventLog, LogView
from pundit.domain.matchtime import MS_PER_MINUTE, format_minute
from pundit.domain.meta import MatchMeta
from pundit.domain.pitch import distance
from pundit.domain.thresholds import (
    DISTANCE_TOLERANCE_M,
    FOOT_SHOT_SPEED_KMH,
    HEADER_SPEED_KMH,
    MAX_DISTANCE_PER_90_M,
    MAX_PASS_SPEED_MS,
    MAX_PLAYER_SPEED_KMH,
    MAX_SUBSTITUTIONS,
    MIN_PASS_SPEED_MS,
    MIN_PLAYERS_ON_PITCH,
)
from pundit.domain.types import PlayType
from pundit.domain.xmodels import xg as xg_model
from pundit.stats.possession import possession_totals
from pundit.stats.select import select

Phase = Literal["pre", "pre_p1", "p1", "half", "p2", "full", "post"]

SAME_TEAM_POSSESSION = frozenset(
    {"pass", "shot", "goal", "offside", "interception", "ball_recovery", "ball_dead"}
)
OPPONENT_POSSESSION = frozenset({"pressure", "foul"})
OUT_OF_PLAY_RESTARTS: frozenset[PlayType] = frozenset({"throw_in", "goal_kick", "corner"})


class MatchSnapshot(BaseModel):
    """Live state for dashboards. Possession fields include the running possession."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    match_id: str
    phase: Phase
    period: int | None
    clock_display: str
    as_of_seq: int
    as_of_ms: int
    score: dict[str, int]
    ball_in_play: bool
    holder_team: str | None
    holder_since_ms: int | None
    possession_ms: dict[str, int]
    ball_in_play_ms: int
    possession_pct: dict[str, float | None]
    on_pitch: dict[str, tuple[str, ...]]
    booked: tuple[str, ...]
    sent_off: tuple[str, ...]
    last_event_id: str | None


class MatchState:
    def __init__(self, meta: MatchMeta) -> None:
        self.meta = meta
        self._log = EventLog(meta)
        self._ball: BallState = INITIAL_BALL_STATE
        self._phase: Phase = "pre"
        self._last_match_ms = 0
        self._last_clock_ms = 0
        self._p1_length_ms: int | None = None
        self._on_pitch: dict[str, set[str]] = {
            meta.home.team.id: set(meta.home.starting_xi),
            meta.away.team.id: set(meta.away.starting_xi),
        }
        self._appeared: dict[str, set[str]] = {t: set(p) for t, p in self._on_pitch.items()}
        self._bench: dict[str, set[str]] = {
            meta.home.team.id: set(meta.home.bench),
            meta.away.team.id: set(meta.away.bench),
        }
        self._booked: set[str] = set()
        self._sent_off: set[str] = set()
        self._subs_used: dict[str, int] = dict.fromkeys(meta.team_ids, 0)
        self._expected_actor: tuple[int, str] | None = None
        self._pending_shot: ShotEvent | None = None
        self._pending_goal: GoalEvent | None = None
        self._required_restart: tuple[frozenset[PlayType], str | None] | None = None
        self._kickoff_goal: GoalEvent | None = None
        self._physical: dict[str, PlayerPhysicalEvent] = {}

    # ------------------------------------------------------------------ read API

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def ball(self) -> BallState:
        return self._ball

    @property
    def ball_carrier(self) -> str | None:
        """Player known to have the ball in the current possession, if any."""
        expected = self._expected_actor
        if expected is None or self._ball.holder is None:
            return None
        return expected[1] if expected[0] == self._ball.possession_id else None

    def view(self) -> LogView:
        return self._log.view()

    def __len__(self) -> int:
        return len(self._log)

    def on_pitch(self, team: str) -> tuple[str, ...]:
        """Players currently on the pitch, sorted (deterministic iteration)."""
        return tuple(sorted(self._on_pitch[team]))

    def bench_available(self, team: str) -> tuple[str, ...]:
        return tuple(sorted(self._bench[team] - self._appeared[team]))

    def has_appeared(self, player: str) -> bool:
        return any(player in players for players in self._appeared.values())

    def is_booked(self, player: str) -> bool:
        return player in self._booked

    def subs_used(self, team: str) -> int:
        return self._subs_used[team]

    def goalkeeper(self, team: str) -> str | None:
        for player in self.on_pitch(team):
            if self.meta.players[player].position == "GK":
                return player
        return None

    def check_complete(self) -> None:
        """Raise if the match is not properly finished (used by the full validator)."""
        if self._phase != "post":
            raise InvalidEventError("INV-02", None, "the match did not end with match_end")
        if self._pending_shot is not None or self._pending_goal is not None:
            raise InvalidEventError("INV-16", None, "goal sequence left incomplete")

    # ------------------------------------------------------------------ write API

    def apply(self, e: Event) -> None:
        self._check_sequence(e)
        self._check_phase(e)
        self._check_time(e)
        self._check_related(e)
        self._check_pass_speed(e)
        self._check_people(e)
        self._check_possession_team(e)
        new_ball, _ = step(self._ball, e)
        self._check_chain(e)
        self._check_goal_sequence(e)
        self._check_restart(e)
        self._check_shot(e)
        self._check_var(e)
        self._check_physical(e)
        self._commit(e, new_ball)
        self._log.append(e)

    # ------------------------------------------------------------------ checks

    def _fail(self, code: str, e: Event, detail: str) -> InvalidEventError:
        return InvalidEventError(code, e.id, detail)

    def _check_sequence(self, e: Event) -> None:
        if e.match_id != self.meta.match_id:
            raise self._fail("INV-01", e, f"match_id {e.match_id} != {self.meta.match_id}")
        if e.seq != len(self._log):
            raise self._fail("INV-01", e, f"expected seq {len(self._log)}, got {e.seq}")

    def _check_phase(self, e: Event) -> None:
        t, phase = e.type, self._phase
        ok = {
            "pre": t == "match_start",
            "pre_p1": t == "period_start" and e.period == 1,
            "p1": t not in ("match_start", "period_start", "match_end") and e.period == 1,
            "half": t == "period_start" and e.period == 2,
            "p2": t not in ("match_start", "period_start", "match_end") and e.period == 2,
            "full": t == "match_end",
            "post": False,
        }[phase]
        if not ok:
            raise self._fail("INV-02", e, f"{t} (period {e.period}) not allowed in phase {phase}")

    def _check_time(self, e: Event) -> None:
        if e.type == "match_start":
            if e.period != 1 or e.clock_ms != 0 or e.match_ms != 0:
                raise self._fail("INV-03", e, "match_start must be at 0 ms of period 1")
            return
        if e.type == "period_start":
            expected = 0 if e.period == 1 else self._p1_length_ms
            if e.clock_ms != 0 or e.match_ms != expected:
                raise self._fail(
                    "INV-03", e, f"period_start must be at clock 0, match_ms {expected}"
                )
            return
        if e.match_ms < self._last_match_ms:
            raise self._fail("INV-03", e, "match_ms went backwards")
        if e.clock_ms < self._last_clock_ms:
            raise self._fail("INV-03", e, "clock_ms went backwards")
        offset = 0 if e.period == 1 else self._p1_length_ms
        if offset is None or e.match_ms != offset + e.clock_ms:
            raise self._fail("INV-03", e, "match_ms inconsistent with period and clock_ms")

    def _related(self, e: Event, index: int, expected: tuple[type, ...]) -> Event:
        rid = e.related_ids[index]
        view = self._log.view()
        if not view.has(rid):
            raise self._fail("INV-05", e, f"related event {rid} does not exist")
        other = view.get(rid)
        if not isinstance(other, expected):
            raise self._fail("INV-05", e, f"related event {rid} has wrong type {other.type}")
        return other

    def _check_related(self, e: Event) -> None:
        if isinstance(e, GoalEvent):
            shot = self._related(e, 0, (ShotEvent,))
            if not (isinstance(shot, ShotEvent) and shot.outcome == "goal" and shot.team == e.team):
                raise self._fail("INV-05", e, "goal must reference a scoring shot of the same team")
        elif isinstance(e, InterceptionEvent):
            cut = self._related(e, 0, (PassEvent,))
            if not (
                isinstance(cut, PassEvent) and cut.outcome == "incomplete" and cut.team != e.team
            ):
                raise self._fail(
                    "INV-05", e, "interception must reference an opponent's incomplete pass"
                )
        elif isinstance(e, BallRecoveryEvent):
            if e.related_ids:
                cause = self._related(e, 0, (ShotEvent, PassEvent))
                if isinstance(cause, PassEvent) and not (
                    cause.outcome == "incomplete" and cause.team != e.team
                ):
                    raise self._fail(
                        "INV-05", e, "a recovery can only follow an opponent's failed pass"
                    )
        elif isinstance(e, CardEvent):
            if e.related_ids:
                foul = self._related(e, 0, (FoulEvent,))
                if foul.player != e.player:
                    raise self._fail("INV-05", e, "card must go to the player who fouled")
        elif isinstance(e, OffsideEvent):
            played = self._related(e, 0, (PassEvent,))
            if played.team != e.team:
                raise self._fail("INV-05", e, "offside must reference a pass of the same team")
            if e.via_var:
                var = self._related(e, 1, (VarCheckEvent,))
                if not (
                    isinstance(var, VarCheckEvent)
                    and var.decision == "overturned"
                    and var.reason == "offside"
                ):
                    raise self._fail("INV-05", e, "VAR offside needs an overturned offside check")
            elif not (isinstance(played, PassEvent) and played.outcome == "offside"):
                raise self._fail("INV-05", e, "offside must reference a pass with outcome offside")
        elif isinstance(e, BallDeadEvent):
            allowed: dict[str, tuple[type, ...]] = {
                "out_of_play": (PassEvent, ShotEvent),
                "foul": (FoulEvent,),
                "offside": (OffsideEvent,),
                "goal": (GoalEvent,),
            }
            if e.reason in allowed:
                cause = self._related(e, 0, allowed[e.reason])
                if isinstance(cause, PassEvent) and cause.outcome != "out":
                    raise self._fail("INV-05", e, "out_of_play must reference a pass that went out")
        elif isinstance(e, VarCheckEvent):
            if e.check_type == "goal":
                self._related(e, 0, (GoalEvent,))
            else:
                self._related(e, 0, (FoulEvent, CardEvent, GoalEvent, ShotEvent))
        elif e.related_ids:
            raise self._fail("INV-05", e, f"{e.type} must not have related_ids")

    def _check_pass_speed(self, e: Event) -> None:
        if isinstance(e, PassEvent):
            assert e.x is not None and e.y is not None
            speed = distance(e.x, e.y, e.end_x, e.end_y) / (e.duration_ms / 1000)
            if not MIN_PASS_SPEED_MS <= speed <= MAX_PASS_SPEED_MS:
                raise self._fail("INV-07", e, f"implied pass speed {speed:.1f} m/s is implausible")

    def _member(self, e: Event, player: str, team: str, what: str) -> None:
        try:
            info = self.meta.player_info(player)
        except UnknownPlayerError as exc:
            raise self._fail("INV-08", e, f"{what} {player} is not in the match") from exc
        if info.team_id != team:
            raise self._fail("INV-08", e, f"{what} {player} does not play for {team}")

    def _present(self, e: Event, player: str, team: str, what: str) -> None:
        self._member(e, player, team, what)
        if player not in self._on_pitch[team]:
            raise self._fail("INV-09", e, f"{what} {player} is not on the pitch")

    def _check_people(self, e: Event) -> None:
        if e.team is None:
            return
        try:
            opponent = self.meta.opponent(e.team)
        except UnknownTeamError as exc:
            raise self._fail("INV-08", e, f"team {e.team} is not in the match") from exc
        if e.player is not None:
            self._present(e, e.player, e.team, "player")
        if isinstance(e, PassEvent) and e.recipient is not None:
            self._present(e, e.recipient, e.team, "recipient")
        if isinstance(e, PressureEvent | TackleEvent):
            self._present(e, e.target_player, opponent, "target_player")
        if isinstance(e, FoulEvent):
            self._present(e, e.fouled_player, opponent, "fouled_player")
        if isinstance(e, GoalEvent) and e.assist is not None:
            self._present(e, e.assist, e.team, "assist")
        if isinstance(e, ShotEvent) and e.goalkeeper is not None:
            self._present(e, e.goalkeeper, opponent, "goalkeeper")
        if isinstance(e, SubstitutionEvent):
            self._member(e, e.player_in, e.team, "player_in")
            if e.player_in not in self._bench[e.team] or e.player_in in self._appeared[e.team]:
                raise self._fail("INV-10", e, f"{e.player_in} is not an unused substitute")
            if self._subs_used[e.team] >= MAX_SUBSTITUTIONS:
                raise self._fail("INV-10", e, f"{e.team} has no substitutions left")
        if isinstance(e, CardEvent):
            assert e.player is not None
            booked = e.player in self._booked
            if e.color == "yellow" and booked:
                raise self._fail("INV-11", e, "a second yellow must be recorded as second_yellow")
            if e.color == "second_yellow" and not booked:
                raise self._fail("INV-11", e, "second_yellow without an earlier yellow")
            if e.color != "yellow" and len(self._on_pitch[e.team]) - 1 < MIN_PLAYERS_ON_PITCH:
                raise self._fail("INV-11", e, f"{e.team} would drop below {MIN_PLAYERS_ON_PITCH}")

    def _check_possession_team(self, e: Event) -> None:
        if e.possession_team is None:
            return
        acting_team_holds = e.type in SAME_TEAM_POSSESSION or (
            isinstance(e, TackleEvent) and e.outcome == "won"
        )
        if acting_team_holds and e.possession_team != e.team:
            raise self._fail("INV-13", e, "possession_team must be the acting team")
        # remaining possession-carrying types: pressure, foul, lost tackle
        if not acting_team_holds and e.possession_team == e.team:
            raise self._fail("INV-13", e, "possession_team must be the opponent")

    def _check_chain(self, e: Event) -> None:
        expected = self._expected_actor
        if expected is None or e.possession_id != expected[0]:
            return
        actor = expected[1]
        if isinstance(e, PassEvent) and not e.is_restart and e.player != actor:
            raise self._fail("INV-15", e, f"ball is with {actor}, not {e.player}")
        if isinstance(e, ShotEvent) and e.player != actor:
            raise self._fail("INV-15", e, f"ball is with {actor}, not {e.player}")
        if isinstance(e, PressureEvent | TackleEvent) and e.target_player != actor:
            raise self._fail("INV-15", e, f"ball is with {actor}, not {e.target_player}")
        if isinstance(e, FoulEvent) and e.fouled_player != actor:
            raise self._fail("INV-15", e, f"ball is with {actor}, not {e.fouled_player}")

    def _check_goal_sequence(self, e: Event) -> None:
        if self._pending_shot is not None:
            shot = self._pending_shot
            if not (
                isinstance(e, GoalEvent)
                and e.related_ids == (shot.id,)
                and e.player == shot.player
                and e.match_ms >= shot.match_ms
            ):
                raise self._fail(
                    "INV-16", e, f"scoring shot {shot.id} must be followed by its goal"
                )
        elif isinstance(e, GoalEvent):
            raise self._fail("INV-16", e, "goal without a scoring shot right before it")
        if self._pending_goal is not None:
            goal = self._pending_goal
            if not (
                isinstance(e, BallDeadEvent)
                and e.reason == "goal"
                and e.related_ids == (goal.id,)
                and e.match_ms == goal.match_ms
            ):
                raise self._fail("INV-16", e, f"goal {goal.id} must be followed by ball_dead(goal)")
        elif isinstance(e, BallDeadEvent) and e.reason == "goal":
            raise self._fail("INV-16", e, "ball_dead(goal) without a goal right before it")

    def _check_restart(self, e: Event) -> None:
        if not (isinstance(e, PassEvent) and e.is_restart):
            return
        required = self._required_restart
        if required is None:
            raise self._fail("INV-17", e, "restart without a stoppage that requires it")
        allowed_types, team = required
        if e.play_type not in allowed_types:
            raise self._fail(
                "INV-17", e, f"expected restart {sorted(allowed_types)}, got {e.play_type}"
            )
        if team is not None and e.team != team:
            raise self._fail("INV-17", e, f"restart must be taken by {team}")

    def _check_shot(self, e: Event) -> None:
        if not isinstance(e, ShotEvent):
            return
        assert e.x is not None and e.y is not None
        if e.xg != xg_model(e.x, e.y, e.body_part, e.situation):
            raise self._fail("INV-18", e, "xg does not match the xG model")
        low, high = HEADER_SPEED_KMH if e.body_part == "head" else FOOT_SHOT_SPEED_KMH
        if not low <= e.speed_kmh <= high:
            raise self._fail("INV-18", e, f"shot speed {e.speed_kmh} km/h out of range")

    def _check_var(self, e: Event) -> None:
        if not (isinstance(e, VarCheckEvent) and e.check_type == "goal"):
            return
        goal = self._kickoff_goal
        if goal is None or e.related_ids != (goal.id,) or self._ball.holder is not None:
            raise self._fail("INV-19", e, "a goal check must review the goal awaiting kick-off")

    def _check_physical(self, e: Event) -> None:
        if not isinstance(e, PlayerPhysicalEvent):
            return
        assert e.player is not None
        previous = self._physical.get(e.player)
        if previous is not None and (
            e.distance_m < previous.distance_m
            or e.top_speed_kmh < previous.top_speed_kmh
            or e.sprints < previous.sprints
        ):
            raise self._fail("INV-20", e, "cumulative physical values must not decrease")
        if e.top_speed_kmh > MAX_PLAYER_SPEED_KMH:
            raise self._fail("INV-20", e, f"top speed {e.top_speed_kmh} km/h is implausible")
        limit = MAX_DISTANCE_PER_90_M * e.match_ms / (90 * MS_PER_MINUTE) + DISTANCE_TOLERANCE_M
        if e.distance_m > limit:
            raise self._fail("INV-20", e, f"distance {e.distance_m:.0f} m is implausible")

    # ------------------------------------------------------------------ commit

    def _commit(self, e: Event, new_ball: BallState) -> None:
        self._ball = new_ball
        t = e.type
        if t == "match_start":
            self._phase = "pre_p1"
        elif t == "period_start":
            self._phase = "p1" if e.period == 1 else "p2"
            self._last_clock_ms = 0
            kickoff_team = self.meta.home.team.id if e.period == 1 else self.meta.away.team.id
            self._required_restart = (frozenset({"kick_off"}), kickoff_team)
            self._kickoff_goal = None
            self._expected_actor = None
        elif t == "period_end":
            if e.period == 1:
                self._phase = "half"
                self._p1_length_ms = e.clock_ms
            else:
                self._phase = "full"
        elif t == "match_end":
            self._phase = "post"
        self._last_match_ms = e.match_ms
        self._last_clock_ms = e.clock_ms

        if isinstance(e, SubstitutionEvent):
            assert e.team is not None and e.player is not None
            self._on_pitch[e.team].discard(e.player)
            self._on_pitch[e.team].add(e.player_in)
            self._appeared[e.team].add(e.player_in)
            self._subs_used[e.team] += 1
        elif isinstance(e, CardEvent):
            assert e.team is not None and e.player is not None
            self._booked.add(e.player)
            if e.color in ("second_yellow", "red"):
                self._sent_off.add(e.player)
                self._on_pitch[e.team].discard(e.player)
        elif isinstance(e, PlayerPhysicalEvent):
            assert e.player is not None
            self._physical[e.player] = e

        # chain continuity (INV-15)
        if isinstance(e, PassEvent):
            if e.outcome == "complete":
                assert e.possession_id is not None and e.recipient is not None
                self._expected_actor = (e.possession_id, e.recipient)
            else:
                self._expected_actor = None
        elif opens_possession(e):
            assert e.possession_id is not None and e.player is not None
            self._expected_actor = (e.possession_id, e.player)
        elif isinstance(e, ShotEvent | BallDeadEvent):
            self._expected_actor = None

        # goal sequence (INV-16) and restarts (INV-17)
        if isinstance(e, ShotEvent):
            self._pending_shot = e if e.outcome == "goal" else None
        elif isinstance(e, GoalEvent):
            self._pending_shot = None
            self._pending_goal = e
        elif isinstance(e, BallDeadEvent):
            self._pending_goal = None
            self._required_restart = self._restart_after(e)
        elif isinstance(e, VarCheckEvent) and e.check_type == "goal" and e.decision == "overturned":
            goal = self._kickoff_goal
            assert goal is not None and goal.team is not None
            self._required_restart = (frozenset({"free_kick"}), self.meta.opponent(goal.team))
            self._kickoff_goal = None
        elif isinstance(e, PassEvent) and e.is_restart:
            self._required_restart = None
            self._kickoff_goal = None

    def _restart_after(self, e: BallDeadEvent) -> tuple[frozenset[PlayType], str | None] | None:
        view = self._log.view()
        if e.reason == "goal":
            goal = view.get(e.related_ids[0])
            assert isinstance(goal, GoalEvent) and goal.team is not None
            self._kickoff_goal = goal
            return frozenset({"kick_off"}), self.meta.opponent(goal.team)
        if e.reason in ("foul", "offside"):
            cause = view.get(e.related_ids[0])
            assert cause.team is not None
            return frozenset({"free_kick"}), self.meta.opponent(cause.team)
        if e.reason == "out_of_play":
            return OUT_OF_PLAY_RESTARTS, None
        return None

    # ------------------------------------------------------------------ snapshot

    def snapshot(self) -> MatchSnapshot:
        view = self.view()
        last = view.last
        score = dict.fromkeys(self.meta.team_ids, 0)
        for goal in select(view, types=("goal",)):
            assert goal.team is not None
            score[goal.team] += 1
        totals = possession_totals(view)
        pct: dict[str, float | None] = {
            team: (100.0 * ms / totals.total_ms if totals.total_ms else None)
            for team, ms in totals.team_ms.items()
        }
        period = last.period if last is not None and self._phase not in ("pre", "pre_p1") else None
        return MatchSnapshot(
            match_id=self.meta.match_id,
            phase=self._phase,
            period=period,
            clock_display=format_minute(last.period, last.clock_ms) if last is not None else "0'",
            as_of_seq=view.last_seq,
            as_of_ms=last.match_ms if last is not None else 0,
            score=score,
            ball_in_play=self._ball.holder is not None,
            holder_team=self._ball.holder,
            holder_since_ms=self._ball.since_ms if self._ball.holder is not None else None,
            possession_ms=totals.team_ms,
            ball_in_play_ms=totals.total_ms,
            possession_pct=pct,
            on_pitch={team: self.on_pitch(team) for team in self.meta.team_ids},
            booked=tuple(sorted(self._booked)),
            sent_off=tuple(sorted(self._sent_off)),
            last_event_id=last.id if last is not None else None,
        )
