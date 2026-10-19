"""Append-only event log and immutable prefix views (docs/day1-design.md section 6.1)."""

from bisect import bisect_left
from collections.abc import Iterator, Sequence

from pundit.domain.events import Event
from pundit.domain.meta import MatchMeta


class EventLog:
    """Append-only list of events with per-type and per-id indexes. Only MatchState writes."""

    def __init__(self, meta: MatchMeta) -> None:
        self.meta = meta
        self._events: list[Event] = []
        self._by_type: dict[str, list[int]] = {}
        self._by_id: dict[str, int] = {}

    def append(self, event: Event) -> None:
        index = len(self._events)
        self._events.append(event)
        self._by_type.setdefault(event.type, []).append(index)
        self._by_id[event.id] = index

    def __len__(self) -> int:
        return len(self._events)

    def view(self, upto: int | None = None) -> "LogView":
        n = len(self._events) if upto is None else max(0, min(upto, len(self._events)))
        return LogView(self, n)


class LogView:
    """Read-only view of the first `n` events of a log. Later appends never change it."""

    __slots__ = ("_log", "n")

    def __init__(self, log: EventLog, n: int) -> None:
        self._log = log
        self.n = n

    @classmethod
    def from_events(cls, meta: MatchMeta, events: Sequence[Event]) -> "LogView":
        log = EventLog(meta)
        for event in events:
            log.append(event)
        return log.view()

    @property
    def meta(self) -> MatchMeta:
        return self._log.meta

    def __len__(self) -> int:
        return self.n

    def __iter__(self) -> Iterator[Event]:
        events = self._log._events
        for index in range(self.n):
            yield events[index]

    def __getitem__(self, index: int) -> Event:
        if not 0 <= index < self.n:
            raise IndexError(index)
        return self._log._events[index]

    def of_type(self, *types: str) -> Iterator[Event]:
        """Events of the given types, in seq order."""
        events = self._log._events
        indexes: list[int] = []
        for event_type in types:
            positions = self._log._by_type.get(event_type, [])
            indexes.extend(positions[: bisect_left(positions, self.n)])
        for index in sorted(indexes):
            yield events[index]

    def get(self, event_id: str) -> Event:
        index = self._log._by_id.get(event_id)
        if index is None or index >= self.n:
            raise KeyError(event_id)
        return self._log._events[index]

    def has(self, event_id: str) -> bool:
        index = self._log._by_id.get(event_id)
        return index is not None and index < self.n

    @property
    def last(self) -> Event | None:
        return self._log._events[self.n - 1] if self.n else None

    @property
    def last_seq(self) -> int:
        return self.n - 1
