"""Real-time replay of a match (docs/day1-design.md section 11.1).

Events are scheduled on an absolute timeline, so a slow consumer never makes the replay
drift. Every event is yielded, including the ones skipped by `from_minute`, so a consumer
that rebuilds state (for example the score) stays correct after a jump.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from pundit.domain.events import (
    BallDeadEvent,
    CardEvent,
    Event,
    FoulEvent,
    GoalEvent,
    OffsideEvent,
    PassEvent,
    PressureEvent,
    ShotEvent,
    SubstitutionEvent,
    TackleEvent,
    VarCheckEvent,
)
from pundit.domain.matchtime import format_clock, format_minute, minute_exact
from pundit.domain.meta import MatchMeta
from pundit.state.match_state import MatchSnapshot


class Clock(Protocol):
    def now(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class FakeClock:
    """Test clock: time only moves when someone sleeps (or a test moves it)."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


@dataclass(frozen=True)
class ReplayItem:
    event: Event
    catching_up: bool  # True while fast-forwarding to `from_minute`


async def replay(
    events: Sequence[Event],
    *,
    speed: float,
    clock: Clock,
    from_minute: float = 0.0,
    halftime_pause_s: float = 2.0,
) -> AsyncIterator[ReplayItem]:
    if speed <= 0:
        raise ValueError("speed must be > 0")
    # Minutes are read like a scoreboard: from 45 onwards means the second half, so first-half
    # stoppage time (45+3' is minute 48 of the clock) never counts as "minute 48".
    start_period = 1 if from_minute < 45 else 2
    live = started = False
    anchor_wall, anchor_ms, pause = 0.0, 0, 0.0
    for event in events:
        live = live or (
            event.period > start_period
            or (
                event.period == start_period
                and minute_exact(event.period, event.clock_ms) >= from_minute
            )
        )
        if not live:
            yield ReplayItem(event, catching_up=True)
            continue
        if not started:
            started, anchor_wall, anchor_ms = True, clock.now(), event.match_ms
        target = anchor_wall + (event.match_ms - anchor_ms) / 1000 / speed + pause
        delay = target - clock.now()
        if delay > 0:
            await clock.sleep(delay)
        yield ReplayItem(event, catching_up=False)
        if event.type == "period_end" and event.period == 1:
            pause += halftime_pause_s


def _name(meta: MatchMeta, player: str | None) -> str:
    return meta.players[player].short_name if player is not None else ""


def _score(meta: MatchMeta, snapshot: MatchSnapshot) -> str:
    home, away = meta.team_ids
    return f"{snapshot.score[home]}-{snapshot.score[away]}"


def describe_event(event: Event, meta: MatchMeta, snapshot: MatchSnapshot) -> str | None:
    """One console line for an event; None for events that are not worth printing."""
    home, away = meta.home.team, meta.away.team
    who = _name(meta, event.player)
    match event:
        case _ if event.type == "match_start":
            text = f"KICK-OFF   {home.name} vs {away.name} | {meta.venue} | {meta.disclaimer}"
        case _ if event.type == "period_start":
            text = f"PERIOD {event.period} START"
        case _ if event.type == "period_end":
            text = f"PERIOD {event.period} END   {_score(meta, snapshot)}"
        case _ if event.type == "match_end":
            text = f"FULL TIME  {home.short_name} {_score(meta, snapshot)} {away.short_name}"
        case PassEvent():
            label = "PASS" if event.play_type == "open_play" else event.play_type.upper()
            if event.outcome == "complete":
                text = f"{label:<10} {who} -> {_name(meta, event.recipient)}"
            else:
                text = f"{label:<10} {who} ({event.outcome})"
        case ShotEvent():
            text = (
                f"SHOT       {who} xG {event.xg:.2f} {event.speed_kmh:.0f} km/h -> {event.outcome}"
            )
        case GoalEvent():
            assist = f" (assist {_name(meta, event.assist)})" if event.assist else ""
            text = f"GOAL       {who}{assist}   {_score(meta, snapshot)}"
        case PressureEvent():
            text = f"PRESSURE   {who} on {_name(meta, event.target_player)}"
        case TackleEvent():
            text = f"TACKLE     {who} on {_name(meta, event.target_player)} ({event.outcome})"
        case FoulEvent():
            text = f"FOUL       {who} on {_name(meta, event.fouled_player)}"
        case CardEvent():
            text = f"CARD       {who} {event.color.replace('_', ' ').upper()}"
        case OffsideEvent():
            text = f"OFFSIDE    {who}{' (VAR)' if event.via_var else ''}"
        case BallDeadEvent():
            text = f"STOPPAGE   {event.reason.replace('_', ' ')}"
        case SubstitutionEvent():
            text = f"SUB        {_name(meta, event.player_in)} for {who}"
        case VarCheckEvent():
            text = f"VAR        {event.check_type} {event.decision} ({event.reason})"
        case _ if event.type == "interception":
            text = f"INTERCEPT  {who}"
        case _ if event.type == "ball_recovery":
            text = f"RECOVERY   {who}"
        case _:
            return None  # player_physical and anything new
    team = meta.team_info(event.team).short_name if event.team is not None else ""
    return f"[{format_minute(event.period, event.clock_ms):>6}] {team:<3} {text}"


def _split(meta: MatchMeta, pct: dict[str, float | None]) -> str:
    home, away = meta.home.team, meta.away.team
    home_pct, away_pct = pct.get(home.id), pct.get(away.id)
    if home_pct is None or away_pct is None:
        return "n/a"
    return f"{home.short_name} {home_pct:.0f}% - {away.short_name} {away_pct:.0f}%"


def status_line(
    meta: MatchMeta,
    snapshot: MatchSnapshot,
    recent_pct: dict[str, float | None] | None = None,
    recent_minutes: float = 10.0,
) -> str:
    """Live possession (ball-in-play time) for the match so far and, if given, a recent window."""
    recent = ""
    if recent_pct is not None:
        recent = f" | LAST {recent_minutes:g}' {_split(meta, recent_pct)}"
    return (
        f"[{snapshot.clock_display:>6}] POSSESSION {_split(meta, snapshot.possession_pct)}"
        f"{recent} | BALL IN PLAY {format_clock(snapshot.ball_in_play_ms)}"
    )
