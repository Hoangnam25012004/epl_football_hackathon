"""MatchBuilder: hand-written fixtures that always pass MatchState (docs section 12.2).

Player references: "H9" = home shirt 9, "A4" = away shirt 4. Times: `.at("12:30")` within the
current period. Possession ids, related ids, xG and pass durations are filled in automatically.
"""

from typing import Any, TypeVar

from pundit.domain.events import (
    BallDeadEvent,
    BallRecoveryEvent,
    CardEvent,
    Event,
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
from pundit.domain.log import LogView
from pundit.domain.pitch import distance, flip
from pundit.domain.xmodels import xg
from pundit.engine.factory import EventFactory
from pundit.engine.rosters import build_meta
from pundit.state.match_state import MatchState

E = TypeVar("E", bound=EventBase)


class MatchBuilder:
    def __init__(
        self,
        home: str = "harbor_lions",
        away: str = "kestrel_athletic",
        *,
        scenario: str = "test",
        seed: int = 0,
        start: bool = True,
    ) -> None:
        self.meta = build_meta(
            match_id=f"{scenario}-{seed}",
            scenario=scenario,
            seed=seed,
            attempt=0,
            home=home,
            away=away,
            kickoff_utc="2026-10-24T14:00:00Z",
            weather="clear",
            referee="Graham Holloway",
            home_attacks_right_p1=True,
            generator_version="test",
        )
        self.factory = EventFactory(self.meta)
        self.state = MatchState(self.meta)
        self.events: list[Any] = []
        self.period = 1
        self.clock = 0
        self.bx, self.by = 52.5, 34.0
        if start:
            self.emit(MatchStartEvent)
            self.emit(PeriodStartEvent)

    # ----------------------------------------------------------- basics

    def at(self, text: str) -> "MatchBuilder":
        minutes, rest = text.split(":")
        seconds, _, millis = rest.partition(".")
        self.clock = int(minutes) * 60_000 + int(seconds) * 1000 + int(millis.ljust(3, "0"))
        return self

    def p(self, ref: str) -> str:
        return f"{self.team_of(ref)}-{int(ref[1:]):02d}"

    def team_of(self, ref: str) -> str:
        return self.meta.home.team.id if ref[0] == "H" else self.meta.away.team.id

    def make(self, cls: type[E], **fields: Any) -> E:
        """Create the next event without applying it (for negative tests)."""
        return self.factory.make(cls, period=self.period, clock_ms=self.clock, **fields)  # type: ignore[arg-type]

    def emit(self, cls: type[E], **fields: Any) -> E:
        event = self.make(cls, **fields)
        self.state.apply(event)  # type: ignore[arg-type]
        self.events.append(event)
        return event

    def view(self) -> LogView:
        return self.state.view()

    def last(self, event_type: str) -> Any:
        for event in reversed(self.events):
            if event.type == event_type:
                return event
        raise LookupError(event_type)

    def poss(self) -> dict[str, Any]:
        ball = self.state.ball
        return {"possession_id": ball.possession_id, "possession_team": ball.holder}

    def new_poss(self, team: str) -> dict[str, Any]:
        return {"possession_id": self.state.ball.possession_id + 1, "possession_team": team}

    # ----------------------------------------------------------- passes

    def _pass(
        self,
        ref: str,
        to: str | None,
        *,
        outcome: str,
        play_type: str,
        x: float | None,
        y: float | None,
        end_x: float | None,
        end_y: float | None,
        **extra: Any,
    ) -> PassEvent:
        team = self.team_of(ref)
        x = self.bx if x is None else x
        y = self.by if y is None else y
        end_x = min(104.0, x + 10.0) if end_x is None else end_x
        end_y = y if end_y is None else end_y
        length = distance(x, y, end_x, end_y)
        duration = extra.pop("duration_ms", max(300, round(length / 15 * 1000)))
        extra.setdefault("height", "ground")
        possession = self.new_poss(team) if play_type != "open_play" else self.poss()
        event = self.emit(
            PassEvent,
            team=team,
            player=self.p(ref),
            x=x,
            y=y,
            outcome=outcome,
            end_x=end_x,
            end_y=end_y,
            duration_ms=duration,
            play_type=play_type,
            recipient=self.p(to) if to else None,
            **possession,
            **extra,
        )
        if outcome == "complete":
            self.bx, self.by = end_x, end_y
        return event

    def pass_(
        self,
        ref: str,
        to: str | None = None,
        *,
        outcome: str | None = None,
        x: float | None = None,
        y: float | None = None,
        end_x: float | None = None,
        end_y: float | None = None,
        **extra: Any,
    ) -> PassEvent:
        outcome = outcome or "complete"
        return self._pass(
            ref,
            to,
            outcome=outcome,
            play_type="open_play",
            x=x,
            y=y,
            end_x=end_x,
            end_y=end_y,
            **extra,
        )

    def restart(
        self,
        play_type: str,
        ref: str,
        to: str | None,
        *,
        x: float,
        y: float,
        end_x: float | None = None,
        end_y: float | None = None,
        outcome: str = "complete",
        **extra: Any,
    ) -> PassEvent:
        return self._pass(
            ref,
            to,
            outcome=outcome,
            play_type=play_type,
            x=x,
            y=y,
            end_x=end_x,
            end_y=end_y,
            **extra,
        )

    def kick_off(self, ref: str, to: str, **extra: Any) -> PassEvent:
        return self.restart("kick_off", ref, to, x=52.5, y=34.0, end_x=44.0, end_y=34.0, **extra)

    def throw_in(
        self, ref: str, to: str, *, x: float = 30.0, y: float = 0.0, **extra: Any
    ) -> PassEvent:
        return self.restart("throw_in", ref, to, x=x, y=y, end_x=x + 8.0, end_y=10.0, **extra)

    def free_kick(
        self, ref: str, to: str, *, x: float = 40.0, y: float = 34.0, **extra: Any
    ) -> PassEvent:
        return self.restart("free_kick", ref, to, x=x, y=y, end_x=x + 12.0, end_y=y, **extra)

    def goal_kick(self, ref: str, to: str, **extra: Any) -> PassEvent:
        return self.restart("goal_kick", ref, to, x=5.0, y=34.0, end_x=20.0, end_y=30.0, **extra)

    def corner(
        self, ref: str, to: str | None = None, *, outcome: str = "complete", **extra: Any
    ) -> PassEvent:
        return self.restart(
            "corner",
            ref,
            to,
            x=105.0,
            y=0.0,
            end_x=97.0,
            end_y=34.0,
            outcome=outcome,
            height="high",
            is_cross=True,
            **extra,
        )

    # ----------------------------------------------------------- duels and ball winning

    def interception(
        self, ref: str, *, x: float | None = None, y: float | None = None
    ) -> InterceptionEvent:
        cut = self.last("pass")
        team = self.team_of(ref)
        if x is None or y is None:
            x, y = flip(cut.end_x, cut.end_y)
        event = self.emit(
            InterceptionEvent,
            team=team,
            player=self.p(ref),
            x=x,
            y=y,
            related_ids=(cut.id,),
            **self.new_poss(team),
        )
        self.bx, self.by = x, y
        return event

    def _target(self, target: str | None) -> str:
        if target is not None:
            return self.p(target)
        carrier = self.state.ball_carrier
        assert carrier is not None, "no known ball carrier; pass target explicitly"
        return carrier

    def pressure(self, ref: str, target: str | None = None) -> PressureEvent:
        x, y = flip(self.bx, self.by)
        return self.emit(
            PressureEvent,
            team=self.team_of(ref),
            player=self.p(ref),
            x=x,
            y=y,
            target_player=self._target(target),
            duration_ms=800,
            **self.poss(),
        )

    def tackle(self, ref: str, target: str | None = None, *, won: bool = True) -> TackleEvent:
        team = self.team_of(ref)
        x, y = flip(self.bx, self.by)
        possession = self.new_poss(team) if won else self.poss()
        event = self.emit(
            TackleEvent,
            team=team,
            player=self.p(ref),
            x=x,
            y=y,
            target_player=self._target(target),
            outcome="won" if won else "lost",
            **possession,
        )
        if won:
            self.bx, self.by = x, y
        return event

    def ball_recovery(self, ref: str, *, x: float = 5.0, y: float = 34.0) -> BallRecoveryEvent:
        team = self.team_of(ref)
        shot = self.last("shot")
        event = self.emit(
            BallRecoveryEvent,
            team=team,
            player=self.p(ref),
            x=x,
            y=y,
            related_ids=(shot.id,),
            **self.new_poss(team),
        )
        self.bx, self.by = x, y
        return event

    def foul(self, ref: str, fouled: str | None = None) -> FoulEvent:
        x, y = flip(self.bx, self.by)
        return self.emit(
            FoulEvent,
            team=self.team_of(ref),
            player=self.p(ref),
            x=x,
            y=y,
            fouled_player=self._target(fouled),
            **self.poss(),
        )

    def card(self, ref: str, color: str = "yellow", *, with_foul: bool = True) -> CardEvent:
        related = (self.last("foul").id,) if with_foul else ()
        return self.emit(
            CardEvent, team=self.team_of(ref), player=self.p(ref), color=color, related_ids=related
        )

    # ----------------------------------------------------------- shots and goals

    def shot(
        self,
        ref: str,
        *,
        outcome: str,
        x: float | None = None,
        y: float | None = None,
        body_part: str = "right_foot",
        situation: str = "open_play",
        speed_kmh: float = 90.0,
    ) -> ShotEvent:
        x = self.bx if x is None else x
        y = self.by if y is None else y
        return self.emit(
            ShotEvent,
            team=self.team_of(ref),
            player=self.p(ref),
            x=x,
            y=y,
            outcome=outcome,
            xg=xg(x, y, body_part, situation),
            body_part=body_part,
            situation=situation,  # type: ignore[arg-type]
            speed_kmh=speed_kmh,
            **self.poss(),
        )

    def goal(self, assist: str | None = None) -> GoalEvent:
        shot = self.last("shot")
        return self.emit(
            GoalEvent,
            team=shot.team,
            player=shot.player,
            assist=self.p(assist) if assist else None,
            related_ids=(shot.id,),
            **self.poss(),
        )

    def ball_dead(self, reason: str, related: str | None = None) -> BallDeadEvent:
        if related is None and reason != "period_end":
            cause = self._last_of("pass", "shot") if reason == "out_of_play" else self.last(reason)
            related = cause.id
        holder = self.state.ball.holder
        return self.emit(
            BallDeadEvent,
            team=holder,
            reason=reason,
            related_ids=(related,) if related else (),
            **self.poss(),
        )

    def _last_of(self, *types: str) -> Any:
        for event in reversed(self.events):
            if event.type in types:
                return event
        raise LookupError(types)

    def offside(self, ref: str, *, via_var: bool = False) -> OffsideEvent:
        played = self.last("pass")
        team = self.team_of(ref)
        related: tuple[str, ...] = (played.id,)
        if via_var:
            related = (played.id, self.last("var_check").id)
            ball = self.state.ball
            possession = {"possession_id": ball.possession_id, "possession_team": ball.last_team}
        else:
            possession = self.poss()
        return self.emit(
            OffsideEvent,
            team=team,
            player=self.p(ref),
            x=played.end_x,
            y=played.end_y,
            via_var=via_var,
            related_ids=related,
            **possession,
        )

    def var_check(self, decision: str = "overturned", reason: str = "offside") -> VarCheckEvent:
        return self.emit(
            VarCheckEvent,
            check_type="goal",
            decision=decision,
            reason=reason,
            duration_ms=90_000,
            related_ids=(self.last("goal").id,),
        )

    # ----------------------------------------------------------- other

    def substitution(self, out_ref: str, in_ref: str) -> SubstitutionEvent:
        return self.emit(
            SubstitutionEvent,
            team=self.team_of(out_ref),
            player=self.p(out_ref),
            player_in=self.p(in_ref),
        )

    def physical(
        self, ref: str, distance_m: float, top_speed_kmh: float, sprints: int
    ) -> PlayerPhysicalEvent:
        return self.emit(
            PlayerPhysicalEvent,
            team=self.team_of(ref),
            player=self.p(ref),
            distance_m=distance_m,
            top_speed_kmh=top_speed_kmh,
            sprints=sprints,
        )

    def end_period(self) -> None:
        """End the current period (ball must be dead). After period 1, start period 2 at 0:00."""
        self.emit(PeriodEndEvent)
        if self.period == 1:
            self.factory.p1_length_ms = self.clock
            self.period = 2
            self.clock = 0
            self.emit(PeriodStartEvent)
            self.bx, self.by = 52.5, 34.0

    def end_match(self) -> None:
        if self.period == 1:
            raise ValueError("end period 1 first")
        self.emit(PeriodEndEvent)
        self.emit(MatchEndEvent)

    @property
    def all_events(self) -> list[Event]:
        return list(self.events)
