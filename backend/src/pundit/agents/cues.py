"""CueDetector (docs/day3-design.md section 4): commentary-worthy events that are not moments.

Stateful but deterministic, like the MomentDetector it runs after: fed the same events (and the
moments each one produced) it returns the same cues with the same ids. `detect_cues` (batch)
runs the very same `feed`. Cues carry Facts only.
"""

from collections.abc import Sequence

from pundit.agents.contracts import Cue, CueKind, Fact, Moment
from pundit.agents.factpack import shot_fact, stat_fact
from pundit.agents.moments import MomentDetector
from pundit.domain.events import (
    Event,
    FoulEvent,
    OffsideEvent,
    PassEvent,
    ShotEvent,
    TackleEvent,
    VarCheckEvent,
)
from pundit.domain.log import EventLog, LogView
from pundit.domain.matchtime import MS_PER_MINUTE, format_minute, minute_exact
from pundit.domain.meta import MatchMeta
from pundit.stats import MetricParams, compute
from pundit.stats.team import is_box_entry

TICK_MINUTES: dict[int, tuple[float, ...]] = {  # a stats update every 5 minutes (28/10)
    1: tuple(float(m) for m in range(5, 45, 5)),
    2: tuple(float(m) for m in range(50, 90, 5)),
}
ADDED_TIME_FROM: dict[int, float] = {1: 45.0, 2: 90.0}
LULL_MS = 1.75 * MS_PER_MINUTE  # no trigger for 1.75 minutes of play -> a "lull" cue, so the
# longest silence, which also waits for the next event, stays within about 2 minutes
PLAY_GAP_MS = 40_000  # a small play (tackle, foul, box entry) is told 40 s after the last line
CONTROL_EVENTS = frozenset({"match_start", "period_start", "period_end", "match_end"})
SHOT_MOMENTS = frozenset({"big_chance", "rocket_shot"})


def cue_id(match_id: str, n: int) -> str:
    return f"{match_id}-q{n:04d}"


class CueDetector:
    def __init__(self, meta: MatchMeta) -> None:
        self.meta = meta
        self._count = 0
        self._ticks: set[float] = set()
        self._added: set[int] = set()
        self._last_trigger_ms: int | None = None

    def _cue(
        self,
        kind: CueKind,
        event: Event,
        *,
        team: str | None = None,
        player: str | None = None,
        facts: Sequence[Fact] = (),
    ) -> Cue:
        self._count += 1
        return Cue(
            id=cue_id(self.meta.match_id, self._count),
            match_id=self.meta.match_id,
            kind=kind,
            team=team,
            player=player,
            period=event.period,
            clock_ms=event.clock_ms,
            display_minute=format_minute(event.period, event.clock_ms),
            seq=event.seq,
            trigger_event_ids=(event.id,),
            facts=tuple(facts),
        )

    def _count_fact(self, view: LogView, metric: str, event: Event) -> Fact:
        result = compute(view, metric, MetricParams(team=event.team))
        return stat_fact(self.meta, result, event.id)

    def feed(self, event: Event, view: LogView, moments: Sequence[Moment] = ()) -> list[Cue]:
        """Cues triggered by `event` (`view` is the log right after applying it); `moments` are
        the moments the same event produced, so a shot that is a moment is not a cue too."""
        found: list[Cue] = []
        minute = minute_exact(event.period, event.clock_ms)
        control = event.type in CONTROL_EVENTS
        if not control:
            for tick in TICK_MINUTES.get(event.period, ()):
                if tick not in self._ticks and minute >= tick:
                    self._ticks.add(tick)
                    found.append(self._cue("stat_tick", event))
            if event.period not in self._added and minute >= ADDED_TIME_FROM[event.period]:
                self._added.add(event.period)
                found.append(self._cue("added_time", event))
        shot_moment = any(
            m.kind in SHOT_MOMENTS and event.id in m.trigger_event_ids for m in moments
        )
        if isinstance(event, ShotEvent) and event.outcome != "goal" and not shot_moment:
            found.append(
                self._cue(
                    "shot",
                    event,
                    team=event.team,
                    player=event.player,
                    facts=[shot_fact(event, "xg"), shot_fact(event, "speed")],
                )
            )
        elif isinstance(event, PassEvent) and event.play_type == "corner":
            found.append(
                self._cue(
                    "corner",
                    event,
                    team=event.team,
                    player=event.player,
                    facts=[self._count_fact(view, "corners", event)],
                )
            )
        elif isinstance(event, OffsideEvent):
            found.append(
                self._cue(
                    "offside",
                    event,
                    team=event.team,
                    player=event.player,
                    facts=[self._count_fact(view, "offsides", event)],
                )
            )
        elif isinstance(event, VarCheckEvent) and not (
            event.check_type == "goal" and event.decision == "overturned"
        ):
            found.append(self._cue("var_review", event))
        if (
            not (moments or found or control)
            and self._last_trigger_ms is not None
            and event.match_ms - self._last_trigger_ms >= PLAY_GAP_MS
        ):
            play = self._small_play(event, view)
            if play is not None:
                found.append(play)
        if moments or found or self._last_trigger_ms is None:
            self._last_trigger_ms = event.match_ms
        elif not control and event.match_ms - self._last_trigger_ms >= LULL_MS:
            found.append(self._cue("lull", event))
            self._last_trigger_ms = event.match_ms
        return found

    def _small_play(self, event: Event, view: LogView) -> Cue | None:
        """A tackle won, a foul or a pass into the box, with its team's count so far."""
        if isinstance(event, TackleEvent) and event.outcome == "won":
            kind: CueKind = "tackle"
            metric = "tackles"
        elif isinstance(event, FoulEvent):
            kind, metric = "foul", "fouls"
        elif is_box_entry(event):
            kind, metric = "box_entry", "box_entries"
        else:
            return None
        return self._cue(
            kind,
            event,
            team=event.team,
            player=event.player,
            facts=[self._count_fact(view, metric, event)],
        )


def detect_cues(meta: MatchMeta, events: Sequence[Event]) -> list[Cue]:
    """Batch detection over a whole match; identical to feeding the events one by one."""
    moments = MomentDetector(meta)
    cues = CueDetector(meta)
    log = EventLog(meta)
    found: list[Cue] = []
    for event in events:
        log.append(event)
        view = log.view()
        found += cues.feed(event, view, moments.feed(event, view))
    return found
