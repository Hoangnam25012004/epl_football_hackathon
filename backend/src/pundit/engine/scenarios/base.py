"""Scenario model (docs/day1-design.md sections 8.2 and 9).

Scenarios are validated when they are created, so a typo in a metric name or an impossible
minute fails at import time instead of in the middle of a demo.
"""

import math
import operator
from collections.abc import Callable
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pundit.domain.ids import TEAM_ID_PATTERN
from pundit.domain.log import LogView
from pundit.domain.matchtime import (
    FULL,
    MS_PER_MINUTE,
    MinuteWindow,
    Window,
    minute_exact,
    parse_display_minute,
)
from pundit.domain.types import Side, Weather
from pundit.engine.rosters import roster_team
from pundit.stats.registry import REGISTRY, MetricParams, compute, get_spec

Pattern = Literal["counter_attack", "cutback", "header_from_corner", "long_shot"]
Op = Literal[">=", ">", "<=", "<", "=="]

_OPS: dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge,
    ">": operator.gt,
    "<=": operator.le,
    "<": operator.lt,
    "==": operator.eq,
}


class TeamProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    control: float = Field(ge=0.0, le=1.0)
    press: float = Field(ge=0.0, le=1.0)
    threat: float = Field(ge=0.0, le=1.0)
    discipline: float = Field(ge=0.0, le=1.0)


class Segment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    period: Literal[1, 2]
    from_minute: float | None = None  # None = from the start of the period
    home: TeamProfile
    away: TeamProfile


class ScriptedGoal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    side: Side
    at: str  # display minute, e.g. "27'" or "90+2'"
    pattern: Pattern
    var_overturn: bool = False

    @property
    def timing(self) -> tuple[int, int]:
        return parse_display_minute(self.at)


class ScriptedCard(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    side: Side
    at: str
    color: Literal["red"] = "red"

    @property
    def timing(self) -> tuple[int, int]:
        return parse_display_minute(self.at)


class Expectation(BaseModel):
    """A checkable statement about a generated match, evaluated with the stats registry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["metric", "diff", "ratio", "score_diff"]
    metric: str = "score"
    side: Side | None = None
    window: Window = FULL
    op: Op
    value: float

    @model_validator(mode="after")
    def _check_expectation(self) -> Self:
        if self.metric not in REGISTRY:
            raise ValueError(f"unknown metric {self.metric!r}")
        if self.kind in ("score_diff",) and self.side is None:
            raise ValueError("score_diff needs a side")
        if self.kind == "metric" and get_spec(self.metric).scope == "team" and self.side is None:
            raise ValueError(f"{self.metric} needs a side")
        return self

    def _value(self, view: LogView, side: Side | None) -> float | None:
        if get_spec(self.metric).scope == "match":
            params = MetricParams(window=self.window)
        else:
            assert side is not None
            params = MetricParams(team=view.meta.team_for(side), window=self.window)
        return compute(view, self.metric, params).value

    def observe(self, view: LogView) -> float | None:
        if self.kind == "metric":
            return self._value(view, self.side)
        if self.kind == "score_diff":
            assert self.side is not None
            other: Side = "away" if self.side == "home" else "home"
            mine = compute(
                view, "score", MetricParams(team=view.meta.team_for(self.side), window=self.window)
            ).value
            theirs = compute(
                view, "score", MetricParams(team=view.meta.team_for(other), window=self.window)
            ).value
            assert mine is not None and theirs is not None
            return mine - theirs
        home, away = self._value(view, "home"), self._value(view, "away")
        if home is None or away is None:
            return None
        if self.kind == "diff":
            return home - away
        if away == 0:
            return math.inf if home > 0 else None
        return home / away

    def check(self, view: LogView) -> tuple[bool, float | None]:
        observed = self.observe(view)
        return observed is not None and _OPS[self.op](observed, self.value), observed

    def describe(self) -> str:
        who = f"({self.side})" if self.side else "(home vs away)"
        return f"{self.kind} {self.metric} {who} {self.window.label()} {self.op} {self.value:g}"


class Expect:
    """Short constructors for expectations."""

    @staticmethod
    def metric(name: str, side: Side | None, window: Window, op: Op, value: float) -> Expectation:
        return Expectation(kind="metric", metric=name, side=side, window=window, op=op, value=value)

    @staticmethod
    def diff(name: str, window: Window, op: Op, value: float) -> Expectation:
        return Expectation(kind="diff", metric=name, window=window, op=op, value=value)

    @staticmethod
    def ratio(name: str, window: Window, op: Op, value: float) -> Expectation:
        return Expectation(kind="ratio", metric=name, window=window, op=op, value=value)

    @staticmethod
    def score_diff(side: Side, op: Op, value: float, window: Window = FULL) -> Expectation:
        return Expectation(kind="score_diff", side=side, window=window, op=op, value=value)


class Scenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(pattern=TEAM_ID_PATTERN)
    title_vi: str
    title_en: str
    home: str
    away: str
    kickoff_utc: str
    weather: Weather
    stoppage_minutes: dict[Literal[1, 2], tuple[int, int]]
    segments: tuple[Segment, ...]
    goal_mode: Literal["scripted_only", "xg_sampled"]
    scripted_goals: tuple[ScriptedGoal, ...] = ()
    scripted_cards: tuple[ScriptedCard, ...] = ()
    expectations: tuple[Expectation, ...] = ()

    @model_validator(mode="after")
    def _check_scenario(self) -> Self:
        if self.home == self.away:
            raise ValueError("home and away must differ")
        roster_team(self.home)
        roster_team(self.away)
        for period in (1, 2):
            low, high = self.stoppage_minutes.get(period, (-1, -1))
            if not 0 <= low <= high <= 10:
                raise ValueError(f"invalid stoppage range for period {period}")
            starts = [s for s in self.segments if s.period == period and s.from_minute is None]
            if len(starts) != 1:
                raise ValueError(f"period {period} needs exactly one segment without from_minute")
        for segment in self.segments:
            if segment.from_minute is not None:
                low_m, high_m = (0, 45) if segment.period == 1 else (45, 90)
                if not low_m < segment.from_minute < high_m:
                    raise ValueError(f"segment start {segment.from_minute} outside its period")
        previous: tuple[int, int] | None = None
        for goal in self.scripted_goals:
            period, clock_ms = goal.timing
            self._check_fits(period, clock_ms, goal.at)
            if previous is not None:
                if (period, clock_ms) <= previous:
                    raise ValueError("scripted goals must be in chronological order")
                if period == previous[0] and clock_ms - previous[1] < 3 * MS_PER_MINUTE:
                    raise ValueError("scripted goals must be at least 3 minutes apart")
            previous = (period, clock_ms)
        for card in self.scripted_cards:
            period, clock_ms = card.timing
            self._check_fits(period, clock_ms, card.at)
        return self

    def _check_fits(self, period: int, clock_ms: int, label: str) -> None:
        min_stoppage = self.stoppage_minutes[1 if period == 1 else 2][0]
        if clock_ms // MS_PER_MINUTE + 2 > 45 + min_stoppage:
            raise ValueError(f"{label} does not fit in period {period} with the minimum stoppage")

    def profiles(self, period: int, clock_ms: int) -> tuple[TeamProfile, TeamProfile]:
        """(home, away) profiles of the segment active at that moment."""
        now = minute_exact(period, clock_ms)
        candidates = [
            s
            for s in self.segments
            if s.period == period and (s.from_minute is None or s.from_minute <= now)
        ]
        active = max(candidates, key=lambda s: -1.0 if s.from_minute is None else s.from_minute)
        return active.home, active.away

    def failed_expectations(self, view: LogView) -> list[str]:
        failures = []
        for expectation in self.expectations:
            ok, observed = expectation.check(view)
            if not ok:
                failures.append(f"{expectation.describe()} (observed {observed})")
        return failures


def window_until(minute: float) -> MinuteWindow:
    return MinuteWindow(end_min=minute)
