"""Synthetic match generator (docs/day1-design.md section 8).

A possession-chain simulator: each loop iteration is one action by the team in possession.
Every event goes through MatchState.apply() as soon as it is created, so an invariant
violation stops generation at the exact event that caused it.
"""

from collections import deque
from dataclasses import dataclass
from typing import Any, TypeVar

from pundit.domain.errors import ScenarioUnsatisfiableError
from pundit.domain.events import (
    BallDeadEvent,
    BallRecoveryEvent,
    CardEvent,
    EventBase,
    FoulEvent,
    GoalEvent,
    InterceptionEvent,
    MatchEndEvent,
    MatchStartEvent,
    OffsideEvent,
    PassEvent,
    PeriodEndEvent,
    PeriodStartEvent,
    PlayerPhysicalEvent,
    PressureEvent,
    ShotEvent,
    SubstitutionEvent,
    TackleEvent,
    VarCheckEvent,
)
from pundit.domain.ids import match_id as make_match_id
from pundit.domain.matchtime import MS_PER_MINUTE, PERIOD_REGULATION_MS, minute_exact
from pundit.domain.meta import MatchMeta
from pundit.domain.pitch import (
    PITCH_LENGTH,
    PITCH_WIDTH,
    clamp,
    distance,
    distance_to_goal,
    flip,
    in_box,
    round_coord,
)
from pundit.domain.thresholds import MAX_SUBSTITUTIONS, MIN_PLAYERS_ON_PITCH
from pundit.domain.types import (
    BodyPart,
    CardColor,
    DeadReason,
    Height,
    Period,
    PlayType,
    Role,
    Situation,
)
from pundit.domain.xmodels import pass_completion_prob
from pundit.domain.xmodels import xg as xg_model
from pundit.engine.factory import EventFactory
from pundit.engine.match import GeneratedMatch
from pundit.engine.patterns import run_pattern
from pundit.engine.rng import Streams
from pundit.engine.rosters import build_meta, load_rosters
from pundit.engine.scenarios import Scenario, ScriptedCard, ScriptedGoal, get_scenario
from pundit.engine.scenarios.base import TeamProfile
from pundit.engine.validate import validate_match
from pundit.state.match_state import MatchState

GENERATOR_VERSION = "0.1.0"

# Weight of each role for receiving the ball in the defensive / middle / final third.
ROLE_ZONE_WEIGHTS: dict[Role, tuple[float, float, float]] = {
    "GK": (0.0, 0.0, 0.0),
    "CB": (3.0, 0.8, 0.2),
    "FB": (2.0, 1.5, 0.8),
    "DM": (2.0, 2.0, 0.5),
    "CM": (1.0, 2.5, 1.2),
    "AM": (0.3, 1.5, 2.0),
    "W": (0.3, 1.2, 2.2),
    "ST": (0.1, 0.6, 2.5),
}
METERS_PER_MINUTE: dict[Role, float] = {
    "GK": 60.0,
    "CB": 105.0,
    "FB": 115.0,
    "DM": 120.0,
    "CM": 125.0,
    "AM": 118.0,
    "W": 112.0,
    "ST": 108.0,
}
RESTART_WAIT_MS: dict[PlayType, tuple[int, int]] = {
    "throw_in": (10_000, 18_000),
    "goal_kick": (18_000, 32_000),
    "free_kick": (22_000, 45_000),
    "corner": (25_000, 40_000),
    "kick_off": (60_000, 90_000),
}
PHYSICAL_EVERY_MS = 5 * MS_PER_MINUTE

# Behaviour parameters, calibrated against EPL-like ranges (see tests/scenario/test_realism.py).
PRESS_BASE, PRESS_FACTOR = 0.15, 0.45  # chance of a pressure event before an action
TACKLE_BASE, TACKLE_FACTOR, TACKLE_WIN = 0.10, 0.25, 0.55
FOUL_RATE = 0.016
SHOT_BOX_BASE, SHOT_BOX_THREAT = 0.08, 0.16
SHOT_OUTSIDE_BASE, SHOT_OUTSIDE_THREAT = 0.015, 0.04
PASS_DX_BASE, PASS_DX_THREAT, PASS_DX_SIGMA = -2.0, 10.0, 9.0
PASS_DY_SIGMA = 11.0
OFFSIDE_BASE, OFFSIDE_THREAT = 0.02, 0.03
CROSS_CHANCE = 0.55
DWELL_MS = (1_000, 3_800)  # time on the ball before an action (outfield player)
CROSS_OUT_SHARE = 0.5  # failed crosses that end up out of play instead of intercepted
BYLINE_DEFLECTION = 0.45  # passes out over the byline touched last by a defender -> corner
OPEN_PLAY_MAX_X = 97.0  # defenders keep open-play passes out of the six-yard area

E = TypeVar("E", bound=EventBase)


@dataclass
class Restart:
    kind: PlayType
    team: str
    x: float
    y: float
    wait_ms: int


@dataclass
class PhysicalAcc:
    since_ms: int
    distance: float = 0.0
    top: float = 0.0
    sprints: int = 0


@dataclass(frozen=True)
class PassPlan:
    end_x: float
    end_y: float
    height: Height
    is_cross: bool = False
    is_through: bool = False


class MatchSim:
    """State of one simulation run. Patterns (engine/patterns.py) drive it for scripted goals."""

    def __init__(
        self, scenario: Scenario, meta: MatchMeta, streams: Streams, *, physical: bool
    ) -> None:
        self.sc = scenario
        self.meta = meta
        self.rng = streams
        self.physical = physical
        self.home, self.away = meta.team_ids
        self.factory = EventFactory(meta)
        self.state = MatchState(meta)
        self.events: list[Any] = []
        self.period: Period = 1
        self.clock = 0
        self.end_ms = 0
        self.p1_length = 0
        self.dead_since = 0
        self.restart: Restart | None = None
        self.carrier: str | None = None
        self.bx, self.by = 52.5, 34.0
        self.last_pass: PassEvent | None = None
        self.goal_pass: PassEvent | None = None
        self.last_goal: GoalEvent | None = None
        self.poss_kind = "kick_off"
        self.poss_start_ms = 0
        self.poss_start_x = 52.5
        self.next_physical_ms = PHYSICAL_EVERY_MS
        self.goals_todo: deque[ScriptedGoal] = deque(
            sorted(scenario.scripted_goals, key=lambda g: g.timing)
        )
        self.cards_todo: list[ScriptedCard] = sorted(
            scenario.scripted_cards, key=lambda c: c.timing
        )
        self.sub_plan: dict[str, list[float]] = {
            team: sorted(streams.subs.uniform(58.0, 84.0) for _ in range(3))
            for team in (self.home, self.away)
        }
        self.phys: dict[str, PhysicalAcc] = {
            player: PhysicalAcc(since_ms=0)
            for team in (self.home, self.away)
            for player in meta.sheet(team).starting_xi
        }

    # ------------------------------------------------------------------ helpers

    @property
    def holder(self) -> str | None:
        return self.state.ball.holder

    def other(self, team: str) -> str:
        return self.meta.opponent(team)

    def profile(self, team: str) -> TeamProfile:
        home, away = self.sc.profiles(self.period, self.clock)
        return home if team == self.home else away

    def poss(self) -> dict[str, Any]:
        ball = self.state.ball
        return {"possession_id": ball.possession_id, "possession_team": ball.holder}

    def new_poss(self, team: str) -> dict[str, Any]:
        return {"possession_id": self.state.ball.possession_id + 1, "possession_team": team}

    def emit(self, cls: type[E], **fields: Any) -> E:
        event = self.factory.make(cls, period=self.period, clock_ms=self.clock, **fields)
        self.state.apply(event)  # type: ignore[arg-type]
        self.events.append(event)
        return event

    def advance(self, ms: float) -> None:
        self.clock += max(0, round(ms))

    def u(self, low: float, high: float) -> float:
        return self.rng.flow.uniform(low, high)

    def wait_for(self, kind: PlayType) -> int:
        low, high = RESTART_WAIT_MS[kind]
        return self.rng.flow.randint(low, high)

    def near_flip(self, x: float, y: float) -> tuple[float, float]:
        """The same spot seen by the other team, with a little jitter."""
        fx, fy = flip(x, y)
        fx = clamp(fx + self.rng.flow.uniform(-1.5, 1.5), 0.5, PITCH_LENGTH - 0.5)
        fy = clamp(fy + self.rng.flow.uniform(-1.5, 1.5), 0.5, PITCH_WIDTH - 0.5)
        return round_coord(fx), round_coord(fy)

    def zone_weight(self, role: Role, x: float) -> float:
        if role == "GK":
            return 0.6 if x < 18.0 else 0.0
        defensive, middle, final = ROLE_ZONE_WEIGHTS[role]
        return defensive if x < 35.0 else middle if x < 70.0 else final

    def pick(
        self,
        team: str,
        x: float,
        *,
        exclude: frozenset[str] | set[str] = frozenset(),
        roles: set[Role] | None = None,
    ) -> str:
        players = [p for p in self.state.on_pitch(team) if p not in exclude]
        if roles is not None:
            preferred = [p for p in players if self.meta.players[p].role in roles]
            if preferred:
                players = preferred
        weights = [self.zone_weight(self.meta.players[p].role, x) for p in players]
        if sum(weights) <= 0:
            weights = [1.0] * len(players)
        return self.rng.players.choices(players, weights=weights)[0]

    def possession_started(self, kind: str) -> None:
        self.poss_kind = kind
        self.poss_start_ms = self.clock
        self.poss_start_x = self.bx
        self.last_pass = None

    def dead(self, team: str, reason: DeadReason, related: str) -> None:
        self.emit(BallDeadEvent, team=team, reason=reason, related_ids=(related,), **self.poss())
        self.dead_since = self.clock

    # ------------------------------------------------------------------ match flow

    def run(self) -> GeneratedMatch:
        self.emit(MatchStartEvent)
        periods: tuple[Period, Period] = (1, 2)
        for period in periods:
            self.play_period(period)
        self.emit(MatchEndEvent)
        if self.goals_todo:
            raise ScenarioUnsatisfiableError(f"scripted goals not played: {list(self.goals_todo)}")
        return GeneratedMatch(meta=self.meta, events=tuple(self.events))

    def play_period(self, period: Period) -> None:
        self.period = period
        self.clock = 0
        if period == 2:
            self.factory.p1_length_ms = self.p1_length
        self.emit(PeriodStartEvent)
        low, high = self.sc.stoppage_minutes[1 if period == 1 else 2]
        self.end_ms = (
            PERIOD_REGULATION_MS
            + self.rng.flow.randint(low, high) * MS_PER_MINUTE
            + self.rng.flow.randint(0, 50_000)
        )
        self.next_physical_ms = PHYSICAL_EVERY_MS
        kick_team = self.home if period == 1 else self.away
        self.restart = Restart("kick_off", kick_team, 52.5, 34.0, 0)
        self.dead_since = 0
        while True:
            if self.holder is None:
                if self.clock >= self.end_ms:
                    break
                self.dead_ball_phase()
            else:
                if self.clock >= self.end_ms:
                    self.advance(self.rng.flow.randint(500, 2_000))  # the whistle follows play
                    self.emit(BallDeadEvent, team=self.holder, reason="period_end", **self.poss())
                    break
                goal = self.due_goal()
                if goal is not None:
                    run_pattern(self, goal)
                else:
                    self.action()
            self.maybe_physical()
        if period == 1:
            self.p1_length = self.clock
        if self.physical:
            self.physical_snapshot()
        self.emit(PeriodEndEvent)

    def due_goal(self) -> ScriptedGoal | None:
        if self.goals_todo and self.goals_todo[0].timing <= (self.period, self.clock):
            return self.goals_todo.popleft()
        return None

    def card_due(self, team: str) -> ScriptedCard | None:
        for card in self.cards_todo:
            if self.meta.team_for(card.side) == team and card.timing <= (self.period, self.clock):
                return card
        return None

    def dead_ball_phase(self) -> None:
        restart = self.restart
        assert restart is not None, "ball is dead without a pending restart"
        self.do_due_subs()
        remaining = restart.wait_ms - (self.clock - self.dead_since)
        if remaining > 0:
            self.advance(remaining)
        if self.clock >= self.end_ms:
            return
        self.take_restart(restart)

    def do_due_subs(self) -> None:
        now = minute_exact(self.period, self.clock)
        for team in (self.home, self.away):
            plan = self.sub_plan[team]
            while plan and plan[0] <= now:
                plan.pop(0)
                self.substitute(team)

    def substitute(self, team: str) -> None:
        if self.state.subs_used(team) >= MAX_SUBSTITUTIONS:
            return
        sheet = self.meta.sheet(team)
        outfield = [
            p
            for p in self.state.on_pitch(team)
            if self.meta.players[p].position != "GK" and p in sheet.starting_xi
        ]
        bench = [
            p for p in self.state.bench_available(team) if self.meta.players[p].position != "GK"
        ]
        if not outfield or not bench:
            return
        leaving = self.rng.subs.choice(outfield)
        same_line = [
            p for p in bench if self.meta.players[p].position == self.meta.players[leaving].position
        ]
        joining = self.rng.subs.choice(same_line or bench)
        self.emit(SubstitutionEvent, team=team, player=leaving, player_in=joining)
        self.phys[joining] = PhysicalAcc(since_ms=self.factory.match_ms(self.period, self.clock))
        self.advance(self.rng.subs.randint(15_000, 30_000))

    def take_restart(self, restart: Restart) -> None:
        team, kind, x, y = restart.team, restart.kind, restart.x, restart.y
        flow = self.rng.flow
        force: str | None = None
        cross = False
        if kind == "kick_off":
            taker = self.pick(team, 52.5, roles={"ST", "AM", "CM"})
            plan = PassPlan(52.5 - flow.uniform(6, 14), 34.0 + flow.uniform(-8, 8), "ground")
            force = "complete"
        elif kind == "throw_in":
            taker = self.pick(team, x, roles={"FB", "W"})
            inside = flow.uniform(6, 18)
            plan = PassPlan(
                x + flow.uniform(-4, 14), inside if y < 34 else PITCH_WIDTH - inside, "low"
            )
        elif kind == "goal_kick":
            taker = self.state.goalkeeper(team) or self.pick(team, 5.0)
            if flow.random() < 0.6:
                plan = PassPlan(flow.uniform(40, 65), flow.uniform(10, 58), "high")
            else:
                plan = PassPlan(flow.uniform(14, 24), flow.uniform(12, 56), "ground")
        elif kind == "free_kick":
            taker = self.pick(team, x, roles={"CM", "DM", "CB", "AM"})
            if x > 70 and flow.random() < 0.5:
                plan = PassPlan(flow.uniform(92, 100), flow.uniform(26, 42), "high", is_cross=True)
                cross = True
            else:
                plan = PassPlan(x + flow.uniform(5, 25), y + flow.gauss(0, 10), "ground")
        else:  # corner
            taker = self.pick(team, 100.0, roles={"W", "AM", "CM"})
            plan = PassPlan(flow.uniform(94, 101), flow.uniform(26, 42), "high", is_cross=True)
            cross = True
        self.carrier = taker
        self.bx, self.by = x, y
        self.possession_started(kind)
        self.restart = None
        self.do_pass(
            team,
            plan.end_x,
            plan.end_y,
            play_type=kind,
            height=plan.height,
            is_cross=cross,
            force=force,
        )

    # ------------------------------------------------------------------ actions

    def action(self) -> None:
        team = self.holder
        assert team is not None and self.carrier is not None
        opp = self.other(team)
        prof, opp_prof = self.profile(team), self.profile(opp)
        role = self.meta.players[self.carrier].role
        flow = self.rng.flow
        self.advance(flow.randint(2_500, 6_000) if role == "GK" else flow.randint(*DWELL_MS))
        if self.clock >= self.end_ms:
            return
        forced_card = self.card_due(opp)
        under = False
        zone = 1.2 if self.bx < 35 else 1.0 if self.bx < 70 else 0.8
        if (
            forced_card is None
            and role != "GK"
            and flow.random() < PRESS_BASE + PRESS_FACTOR * opp_prof.press * zone
        ):
            px, py = self.near_flip(self.bx, self.by)
            presser = self.pick(opp, px)
            self.emit(
                PressureEvent,
                team=opp,
                player=presser,
                x=px,
                y=py,
                target_player=self.carrier,
                duration_ms=flow.randint(400, 1_200),
                **self.poss(),
            )
            self.advance(flow.randint(200, 600))
            under = True
            if flow.random() < TACKLE_BASE + TACKLE_FACTOR * opp_prof.press:
                self.advance(flow.randint(200, 600))
                if flow.random() < TACKLE_WIN:
                    self.emit(
                        TackleEvent,
                        team=opp,
                        player=presser,
                        x=px,
                        y=py,
                        target_player=self.carrier,
                        outcome="won",
                        **self.new_poss(opp),
                    )
                    self.carrier = presser
                    self.bx, self.by = px, py
                    self.possession_started("tackle")
                    return
                self.emit(
                    TackleEvent,
                    team=opp,
                    player=presser,
                    x=px,
                    y=py,
                    target_player=self.carrier,
                    outcome="lost",
                    **self.poss(),
                )
        if not in_box(self.bx, self.by):
            p_foul = FOUL_RATE * (1.6 - opp_prof.discipline) * (1.5 if under else 1.0)
            if forced_card is not None or flow.random() < p_foul:
                self.do_foul(team, forced_card)
                return
        if self.wants_shot(prof, role):
            self.do_shot(team)
            return
        plan = self.plan_pass(prof, role)
        self.do_pass(
            team,
            plan.end_x,
            plan.end_y,
            play_type="open_play",
            height=plan.height,
            is_cross=plan.is_cross,
            is_through=plan.is_through,
            under_pressure=under,
        )

    def plan_pass(self, prof: TeamProfile, role: Role) -> PassPlan:
        x, y, flow = self.bx, self.by, self.rng.flow
        if role == "GK":
            if flow.random() < 0.55:
                return PassPlan(flow.uniform(35, 65), flow.uniform(8, 60), "high")
            return PassPlan(flow.uniform(12, 25), flow.uniform(10, 58), "ground")
        if x > 80 and (y < 14 or y > 54) and flow.random() < CROSS_CHANCE:
            return PassPlan(flow.uniform(90, 99), flow.uniform(26, 42), "high", is_cross=True)
        dx = flow.gauss(PASS_DX_BASE + PASS_DX_THREAT * prof.threat, PASS_DX_SIGMA)
        end_x = clamp(x + dx, 1.0, OPEN_PLAY_MAX_X)
        end_y = clamp(y + flow.gauss(0, PASS_DY_SIGMA), 1.0, 67.0)
        if distance(x, y, end_x, end_y) < 4.0:
            end_x = clamp(x + (6.0 if x < 98 else -6.0), 1.0, 104.0)
        length = distance(x, y, end_x, end_y)
        height: Height
        if length > 30:
            height = flow.choices(["high", "low", "ground"], weights=[0.5, 0.3, 0.2])[0]  # type: ignore[assignment]
        else:
            height = "ground" if flow.random() < 0.85 else "low"
        through = dx > 15 and end_x > 70 and height != "high" and flow.random() < 0.3
        return PassPlan(end_x, end_y, height, is_through=through)

    def do_pass(
        self,
        team: str,
        end_x: float,
        end_y: float,
        *,
        play_type: PlayType,
        height: Height,
        is_cross: bool = False,
        is_through: bool = False,
        under_pressure: bool = False,
        force: str | None = None,
        recipient: str | None = None,
        intercept_frac: float | None = None,
        interceptor_roles: set[Role] | None = None,
    ) -> PassEvent:
        assert self.carrier is not None
        x, y = self.bx, self.by
        end_x = round_coord(clamp(end_x, 1.0, 104.0))
        end_y = round_coord(clamp(end_y, 1.0, 67.0))
        if distance(x, y, end_x, end_y) < 3.0:
            end_x = round_coord(clamp(x + (4.0 if x < 100 else -4.0), 1.0, 104.0))
        prof = self.profile(team)
        flow = self.rng.flow
        if force is not None:
            outcome = force
        elif (
            play_type in ("open_play", "free_kick")
            and not is_cross
            and end_x - x > 5
            and end_x >= 80
            and flow.random() < OFFSIDE_BASE + OFFSIDE_THREAT * prof.threat
        ):
            outcome = "offside"
        else:
            p_complete = pass_completion_prob(
                x,
                y,
                end_x,
                end_y,
                under_pressure=under_pressure,
                height=height,
                is_cross=is_cross,
                control=prof.control,
            )
            out_share = CROSS_OUT_SHARE if is_cross else 0.35
            if flow.random() < p_complete:
                outcome = "complete"
            elif flow.random() < out_share:
                outcome = "out"
            else:
                outcome = "incomplete"
        if outcome == "out":
            end_x, end_y = self.exit_point(x, y, end_x, end_y)
        speed = flow.uniform(15, 24) if height == "high" else flow.uniform(11, 20)
        duration = max(300, round(distance(x, y, end_x, end_y) / speed * 1000))
        mate = None
        if outcome == "complete":
            mate = recipient or self.pick(team, end_x, exclude={self.carrier})
        possession = self.new_poss(team) if play_type != "open_play" else self.poss()
        sent = self.emit(
            PassEvent,
            team=team,
            player=self.carrier,
            x=x,
            y=y,
            outcome=outcome,
            end_x=end_x,
            end_y=end_y,
            duration_ms=duration,
            height=height,
            play_type=play_type,
            recipient=mate,
            under_pressure=under_pressure,
            is_cross=is_cross,
            is_through_ball=is_through,
            **possession,
        )
        if outcome == "complete":
            self.advance(duration)
            self.carrier = mate
            self.bx, self.by = end_x, end_y
            self.last_pass = sent
        elif outcome == "incomplete":
            frac = intercept_frac if intercept_frac is not None else flow.uniform(0.35, 0.95)
            ix, iy = x + (end_x - x) * frac, y + (end_y - y) * frac
            self.advance(max(100, round(duration * frac)))
            opp = self.other(team)
            fx, fy = flip(ix, iy)
            fx = round_coord(clamp(fx, 0.5, PITCH_LENGTH - 0.5))
            fy = round_coord(clamp(fy, 0.5, PITCH_WIDTH - 0.5))
            cutter = self.pick(opp, fx, roles=interceptor_roles)
            self.emit(
                InterceptionEvent,
                team=opp,
                player=cutter,
                x=fx,
                y=fy,
                related_ids=(sent.id,),
                **self.new_poss(opp),
            )
            self.carrier = cutter
            self.bx, self.by = fx, fy
            self.possession_started("interception")
        elif outcome == "out":
            self.advance(duration)
            self.dead(team, "out_of_play", sent.id)
            deflected = is_cross or flow.random() < BYLINE_DEFLECTION
            self.restart_after_out(team, end_x, end_y, deflected=deflected)
        else:  # offside
            self.advance(duration)
            flagged = self.pick(team, end_x, exclude={self.carrier}, roles={"ST", "W", "AM"})
            offside = self.emit(
                OffsideEvent,
                team=team,
                player=flagged,
                x=end_x,
                y=end_y,
                related_ids=(sent.id,),
                **self.poss(),
            )
            self.dead(team, "offside", offside.id)
            fx, fy = flip(end_x, end_y)
            self.restart = Restart(
                "free_kick",
                self.other(team),
                round_coord(fx),
                round_coord(fy),
                self.wait_for("free_kick"),
            )
        return sent

    def exit_point(self, x: float, y: float, end_x: float, end_y: float) -> tuple[float, float]:
        """Where a pass that goes out of play crosses the line (at least 3 m from the passer)."""
        if end_x > 95.0:
            exit_x, exit_y = PITCH_LENGTH, round_coord(clamp(end_y, 1.0, 67.0))
        else:
            exit_x, exit_y = (
                round_coord(clamp(end_x, 1.0, 104.0)),
                (0.0 if end_y < 34.0 else PITCH_WIDTH),
            )
        if distance(x, y, exit_x, exit_y) < 3.0:
            exit_x = round_coord(clamp(x + (5.0 if x < 99 else -5.0), 1.0, 104.0))
        return exit_x, exit_y

    def restart_after_out(
        self, team: str, exit_x: float, exit_y: float, *, deflected: bool = False
    ) -> None:
        """Over the byline: corner if a defender touched it last, otherwise a goal kick."""
        opp = self.other(team)
        if exit_x >= PITCH_LENGTH:
            if deflected:
                self.corner_for(team, exit_y)
                return
            y = round_coord(self.u(24, 44))
            self.restart = Restart("goal_kick", opp, 5.0, y, self.wait_for("goal_kick"))
        else:
            fx, fy = flip(exit_x, exit_y)
            self.restart = Restart("throw_in", opp, round_coord(fx), fy, self.wait_for("throw_in"))

    def corner_for(self, team: str, shot_y: float) -> None:
        corner_y = 0.0 if shot_y < 34.0 else PITCH_WIDTH
        self.restart = Restart("corner", team, PITCH_LENGTH, corner_y, self.wait_for("corner"))

    def do_foul(self, team: str, forced_card: ScriptedCard | None) -> None:
        assert self.carrier is not None
        opp = self.other(team)
        fx, fy = self.near_flip(self.bx, self.by)
        fouler = self.pick(opp, fx)
        foul = self.emit(
            FoulEvent,
            team=opp,
            player=fouler,
            x=fx,
            y=fy,
            fouled_player=self.carrier,
            **self.poss(),
        )
        self.dead(team, "foul", foul.id)
        color = self.card_color(opp, fouler, forced_card)
        if color is not None:
            self.advance(self.rng.cards.randint(8_000, 20_000))
            self.emit(CardEvent, team=opp, player=fouler, color=color, related_ids=(foul.id,))
        self.restart = Restart("free_kick", team, self.bx, self.by, self.wait_for("free_kick"))

    def card_color(self, team: str, player: str, forced: ScriptedCard | None) -> CardColor | None:
        can_lose_player = len(self.state.on_pitch(team)) - 1 >= MIN_PLAYERS_ON_PITCH + 1
        if forced is not None:
            self.cards_todo.remove(forced)
            return "red" if can_lose_player else None
        roll = self.rng.cards.random()
        if roll < 0.004:
            return "red" if can_lose_player else None
        if roll < 0.164:
            if self.state.is_booked(player):
                return "second_yellow" if can_lose_player else None
            return "yellow"
        return None

    def wants_shot(self, prof: TeamProfile, role: Role) -> bool:
        if role == "GK":
            return False
        x, y = self.bx, self.by
        if in_box(x, y):
            chance = SHOT_BOX_BASE + SHOT_BOX_THREAT * prof.threat
        elif x >= 70 and abs(y - 34.0) < 25:
            chance = SHOT_OUTSIDE_BASE + SHOT_OUTSIDE_THREAT * prof.threat
        else:
            return False
        return self.rng.flow.random() < chance

    def situation(self) -> Situation:
        elapsed = self.clock - self.poss_start_ms
        if elapsed < 15_000:
            if self.poss_kind == "corner":
                return "corner"
            if self.poss_kind == "free_kick":
                return "set_piece"
            if self.poss_kind in ("interception", "tackle", "ball_recovery") and (
                self.poss_start_x < 52.5
            ):
                return "counter"
        return "open_play"

    def shot_outcome(self, xg_value: float) -> str:
        flow = self.rng.flow
        if self.sc.goal_mode == "xg_sampled" and flow.random() < xg_value:
            return "goal"
        roll = flow.random()
        if roll < 0.30:
            return "blocked"
        if roll < 0.66:
            return "off_target"
        if roll < 0.96:
            return "saved"
        return "post"

    def do_shot(
        self,
        team: str,
        *,
        body: BodyPart | None = None,
        situation: Situation | None = None,
        outcome: str | None = None,
        resolve: bool = True,
    ) -> ShotEvent:
        assert self.carrier is not None
        x, y = self.bx, self.by
        shooter = self.carrier
        info = self.meta.players[shooter]
        flow = self.rng.flow
        if body is None:
            headed = (
                self.last_pass is not None
                and self.last_pass.is_cross
                and self.last_pass.height == "high"
                and flow.random() < 0.6
            )
            body = "head" if headed else ("left_foot" if info.foot == "left" else "right_foot")
        if situation is None:
            situation = self.situation()
        xg_value = xg_model(x, y, body, situation)
        dist = distance_to_goal(x, y)
        if body == "head":
            speed = round(flow.uniform(35, 60), 1)
        else:
            speed = round(clamp(flow.uniform(70, 115) - 0.8 * max(0.0, dist - 11.0), 45, 130), 1)
        if outcome is None:
            outcome = self.shot_outcome(xg_value)
        shot = self.emit(
            ShotEvent,
            team=team,
            player=shooter,
            x=x,
            y=y,
            outcome=outcome,
            xg=xg_value,
            body_part=body,
            situation=situation,
            speed_kmh=speed,
            goalkeeper=self.state.goalkeeper(self.other(team)),
            **self.poss(),
        )
        if resolve:
            self.after_shot(shot, max(150, round(dist / (speed / 3.6) * 1000)))
        return shot

    def recover(self, team: str, player: str, x: float, y: float, related: str) -> None:
        self.emit(
            BallRecoveryEvent,
            team=team,
            player=player,
            x=x,
            y=y,
            related_ids=(related,),
            **self.new_poss(team),
        )
        self.carrier = player
        self.bx, self.by = x, y
        self.possession_started("ball_recovery")

    def after_shot(self, shot: ShotEvent, flight_ms: int) -> None:
        assert shot.team is not None and shot.x is not None and shot.y is not None
        team, opp, flow = shot.team, self.other(shot.team), self.rng.flow
        if shot.outcome == "goal":
            self.advance(flight_ms)
            self.score_goal(shot)
            return
        if shot.outcome == "off_target":
            self.advance(flight_ms)
            self.dead(team, "out_of_play", shot.id)
            y = round_coord(self.u(24, 44))
            self.restart = Restart("goal_kick", opp, 5.0, y, self.wait_for("goal_kick"))
            return
        if shot.outcome == "saved":
            self.advance(flight_ms)
            if shot.goalkeeper is not None and flow.random() < 0.65:
                gx, gy = round_coord(self.u(2, 6)), round_coord(self.u(30, 38))
                self.recover(opp, shot.goalkeeper, gx, gy, shot.id)
            else:
                self.dead(team, "out_of_play", shot.id)
                self.corner_for(team, shot.y)
            return
        self.advance(flow.randint(150, 400) if shot.outcome == "blocked" else flight_ms)
        roll = flow.random()
        if roll < 0.45:
            fx, fy = self.near_flip(shot.x, shot.y)
            self.recover(opp, self.pick(opp, fx), fx, fy, shot.id)
        elif roll < 0.70:
            self.dead(team, "out_of_play", shot.id)
            if shot.outcome == "blocked":
                self.corner_for(team, shot.y)
            else:
                y = round_coord(self.u(24, 44))
                self.restart = Restart("goal_kick", opp, 5.0, y, self.wait_for("goal_kick"))
        else:  # the attacking team keeps the loose ball
            self.carrier = self.pick(team, shot.x)
            self.bx = round_coord(clamp(shot.x - self.u(3, 10), 1.0, 104.0))
            self.by = round_coord(clamp(shot.y + self.u(-6, 6), 1.0, 67.0))
            self.last_pass = None

    def score_goal(self, shot: ShotEvent) -> GoalEvent:
        assert shot.team is not None and shot.player is not None
        assist = None
        feed = self.last_pass
        if (
            feed is not None
            and feed.recipient == shot.player
            and feed.possession_id == shot.possession_id
        ):
            assist = feed.player
        goal = self.emit(
            GoalEvent,
            team=shot.team,
            player=shot.player,
            assist=assist,
            related_ids=(shot.id,),
            **self.poss(),
        )
        self.dead(shot.team, "goal", goal.id)
        self.goal_pass = feed
        self.last_goal = goal
        self.restart = Restart(
            "kick_off", self.other(shot.team), 52.5, 34.0, self.wait_for("kick_off")
        )
        return goal

    def var_overturn(self, goal: GoalEvent, feed: PassEvent) -> None:
        """VAR rules a goal out for offside (docs section 8.5)."""
        assert goal.team is not None and goal.player is not None
        patterns = self.rng.patterns
        self.advance(patterns.randint(20_000, 40_000))
        check_ms = patterns.randint(60_000, 150_000)
        self.advance(check_ms)
        var = self.emit(
            VarCheckEvent,
            check_type="goal",
            decision="overturned",
            reason="offside",
            duration_ms=check_ms,
            related_ids=(goal.id,),
        )
        ball = self.state.ball
        self.emit(
            OffsideEvent,
            team=goal.team,
            player=goal.player,
            x=feed.end_x,
            y=feed.end_y,
            via_var=True,
            related_ids=(feed.id, var.id),
            possession_id=ball.possession_id,
            possession_team=ball.last_team,
        )
        fx, fy = flip(feed.end_x, feed.end_y)
        self.dead_since = self.clock
        self.restart = Restart(
            "free_kick",
            self.other(goal.team),
            round_coord(fx),
            round_coord(fy),
            patterns.randint(15_000, 30_000),
        )

    # ------------------------------------------------------------------ physical

    def maybe_physical(self) -> None:
        if not self.physical or self.clock < self.next_physical_ms:
            return
        self.physical_snapshot()
        while self.next_physical_ms <= self.clock:
            self.next_physical_ms += PHYSICAL_EVERY_MS

    def physical_snapshot(self) -> None:
        now = self.factory.match_ms(self.period, self.clock)
        rng = self.rng.physical
        for team in (self.home, self.away):
            intensity = 0.9 + 0.2 * self.profile(team).press
            for player in self.state.on_pitch(team):
                acc = self.phys[player]
                minutes = (now - acc.since_ms) / MS_PER_MINUTE
                if minutes <= 0:
                    continue
                role = self.meta.players[player].role
                acc.distance += (
                    minutes * METERS_PER_MINUTE[role] * intensity * rng.uniform(0.95, 1.05)
                )
                if role == "GK":
                    new_sprints = rng.randint(0, 1) if minutes >= 2 else 0
                    top_range = (18.0, 25.0)
                else:
                    new_sprints = rng.randint(0, max(1, round(3 * minutes / 5)))
                    top_range = (27.0, 35.5)
                if new_sprints:
                    acc.sprints += new_sprints
                    acc.top = max(acc.top, round(rng.uniform(*top_range), 1))
                acc.since_ms = now
                self.emit(
                    PlayerPhysicalEvent,
                    team=team,
                    player=player,
                    distance_m=round(acc.distance, 1),
                    top_speed_kmh=acc.top,
                    sprints=acc.sprints,
                )


def _simulate(scenario: Scenario, seed: int, attempt: int, *, physical: bool) -> GeneratedMatch:
    streams = Streams.create(seed, attempt)
    rosters = load_rosters()
    meta = build_meta(
        match_id=make_match_id(scenario.key, seed),
        scenario=scenario.key,
        seed=seed,
        attempt=attempt,
        home=scenario.home,
        away=scenario.away,
        kickoff_utc=scenario.kickoff_utc,
        weather=scenario.weather,
        referee=streams.meta.choice(rosters.referees),
        home_attacks_right_p1=streams.meta.random() < 0.5,
        generator_version=GENERATOR_VERSION,
    )
    return MatchSim(scenario, meta, streams, physical=physical).run()


def generate(
    scenario: Scenario | str,
    seed: int,
    *,
    physical: bool = True,
    max_attempts: int = 20,
) -> GeneratedMatch:
    """Generate a match. Same scenario + seed always gives the same match, byte for byte."""
    spec = get_scenario(scenario) if isinstance(scenario, str) else scenario
    failures: list[str] = []
    for attempt in range(max_attempts):
        match = _simulate(spec, seed, attempt, physical=physical)
        validate_match(match.meta, match.events)
        failures = spec.failed_expectations(match.view())
        if not failures:
            return match
    raise ScenarioUnsatisfiableError(
        f"{spec.key} seed {seed}: no attempt satisfied the expectations; last failures: {failures}"
    )
