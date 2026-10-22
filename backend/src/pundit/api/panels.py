"""Read-only panels of the studio UI, computed on a session's current view (docs/day4-design.md
sections 3.3-3.5): the momentum timeline, the stats table and events looked up by id.

Every number comes from the Stats Tools of Day 1 and is shown with Day 1 `display()`, so the
table agrees with claims and talking points. Only applied events are read: a panel never shows
the future of a replay. Results are cached per `as_of_seq` (the replay is the only writer).
"""

from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from pundit.agents.contracts import Moment
from pundit.domain.events import Event
from pundit.domain.log import LogView
from pundit.domain.matchtime import FULL, H1, H2, MS_PER_MINUTE, MinuteWindow
from pundit.stats.registry import MetricParams, compute, get_spec
from pundit.stats.result import display
from pundit.stats.tactical import momentum_series

Period = Literal["FULL", "H1", "H2"]
PERIOD_WINDOWS: dict[Period, MinuteWindow] = {"FULL": FULL, "H1": H1, "H2": H2}
STATS_PANEL = (
    "possession",
    "shots",
    "shots_on_target",
    "xg",
    "corners",
    "fouls",
    "yellow_cards",
    "red_cards",
    "offsides",
    "pass_accuracy",
    "ppda",
    "box_entries",
)
MARKER_KINDS = frozenset({"goal", "goal_overturned", "red_card", "period_end", "full_time"})
SILENT_EVENTS = frozenset({"player_physical"})  # never broadcast, never listed
MAX_IDS = 200
MAX_LAST = 300
NO_VALUE = "—"
CACHE_SIZE = 16


class _View(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TimelinePoint(_View):
    minute: int  # minutes of match time played (both halves, stoppage time included)
    value: float  # momentum of the home team over the window, -1..1
    display: str


class TimelineMarker(_View):
    moment_id: str
    kind: str
    minute: float  # on the same axis as the points
    display_minute: str
    team: str | None


class TimelineView(_View):
    as_of_seq: int
    window_minutes: float
    home: str
    away: str
    half_time_minute: float | None  # where the second half starts on the axis
    points: list[TimelinePoint]
    markers: list[TimelineMarker]


class StatCell(_View):
    value: float | None
    display: str


class StatsRow(_View):
    metric: str
    label_vi: str
    label_en: str
    unit: str
    home: StatCell
    away: StatCell
    evidence_count: int


class StatsPanel(_View):
    as_of_seq: int
    period: Period
    home: str
    away: str
    rows: list[StatsRow]


class EventsPage(_View):
    as_of_seq: int
    events: list[Event]
    missing: list[str]


def momentum_display(value: float) -> str:
    """ "+0.42" / "-0.18"; a value that rounds to zero is "+0.00", never "-0.00"."""
    shown = f"{value:+.2f}"
    return "+0.00" if shown == "-0.00" else shown


def timeline(
    view: LogView, moments: Sequence[Moment], window_minutes: float = 10.0
) -> TimelineView:
    home, away = view.meta.team_ids
    points = []
    for minute, raw in momentum_series(view, home, window_minutes=window_minutes):
        value = round(raw, 4)  # the chart and the label read the same number
        points.append(TimelinePoint(minute=minute, value=value, display=momentum_display(value)))
    markers = [
        TimelineMarker(
            moment_id=moment.id,
            kind=moment.kind,
            minute=round(view[moment.seq].match_ms / MS_PER_MINUTE, 2),
            display_minute=moment.display_minute,
            team=moment.team,
        )
        for moment in moments
        if moment.kind in MARKER_KINDS and moment.seq <= view.last_seq
    ]
    second_half = next((e for e in view.of_type("period_start") if e.period == 2), None)
    half_time = round(second_half.match_ms / MS_PER_MINUTE, 2) if second_half else None
    return TimelineView(
        as_of_seq=view.last_seq,
        window_minutes=window_minutes,
        home=home,
        away=away,
        half_time_minute=half_time,
        points=points,
        markers=markers,
    )


def _cell(view: LogView, metric: str, team: str, window: MinuteWindow) -> tuple[StatCell, int]:
    try:
        result = compute(view, metric, MetricParams(team=team, window=window))
    except ValueError:  # a metric that cannot apply to this window yet
        return StatCell(value=None, display=NO_VALUE), 0
    if result.value is None:
        return StatCell(value=None, display=NO_VALUE), 0
    return StatCell(value=result.value, display=display(result)), len(result.evidence_event_ids)


def stats_panel(view: LogView, period: Period) -> StatsPanel:
    home, away = view.meta.team_ids
    window = PERIOD_WINDOWS[period]
    rows: list[StatsRow] = []
    for metric in STATS_PANEL:
        spec = get_spec(metric)
        home_cell, home_evidence = _cell(view, metric, home, window)
        away_cell, away_evidence = _cell(view, metric, away, window)
        rows.append(
            StatsRow(
                metric=metric,
                label_vi=spec.description_vi,
                label_en=spec.description_en,
                unit=spec.unit,
                home=home_cell,
                away=away_cell,
                evidence_count=home_evidence + away_evidence,
            )
        )
    return StatsPanel(as_of_seq=view.last_seq, period=period, home=home, away=away, rows=rows)


def events_by_id(view: LogView, ids: Sequence[str]) -> EventsPage:
    """Applied events among `ids`, in the order asked; the others are `missing`."""
    found: list[Event] = []
    missing: list[str] = []
    for event_id in dict.fromkeys(ids):  # unique, order kept
        if view.has(event_id) and view.get(event_id).type not in SILENT_EVENTS:
            found.append(view.get(event_id))
        else:
            missing.append(event_id)
    return EventsPage(as_of_seq=view.last_seq, events=found, missing=missing)


def last_events(view: LogView, count: int) -> EventsPage:
    """The last `count` broadcast events (no `player_physical`), oldest first."""
    found: list[Event] = []
    for index in range(len(view) - 1, -1, -1):
        if len(found) >= count:
            break
        event = view[index]
        if event.type not in SILENT_EVENTS:
            found.append(event)
    found.reverse()
    return EventsPage(as_of_seq=view.last_seq, events=found, missing=[])


class PanelCache:
    """Last results per (panel, parameters, as_of_seq); a new event makes them stale."""

    def __init__(self, size: int = CACHE_SIZE) -> None:
        self._size = size
        self._items: OrderedDict[tuple[object, ...], object] = OrderedDict()

    def get[T](self, key: tuple[object, ...], build: Callable[[], T]) -> T:
        if key in self._items:
            self._items.move_to_end(key)
            found: T = self._items[key]  # type: ignore[assignment]
            return found
        value = build()
        self._items[key] = value
        while len(self._items) > self._size:
            self._items.popitem(last=False)
        return value
