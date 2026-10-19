"""EventFactory: the only code that assigns id, seq, match_ms, minute and second."""

from typing import Any, TypeVar

from pundit.domain.events import EventBase
from pundit.domain.ids import event_id
from pundit.domain.matchtime import minute_fields
from pundit.domain.meta import MatchMeta
from pundit.domain.types import Period

E = TypeVar("E", bound=EventBase)


class EventFactory:
    def __init__(self, meta: MatchMeta) -> None:
        self.meta = meta
        self.seq = 0
        self.p1_length_ms: int | None = None

    def match_ms(self, period: int, clock_ms: int) -> int:
        if period == 1:
            return clock_ms
        if self.p1_length_ms is None:
            raise ValueError("the length of the first half is not known yet")
        return self.p1_length_ms + clock_ms

    def make(self, cls: type[E], *, period: Period, clock_ms: int, **fields: Any) -> E:
        minute, second = minute_fields(period, clock_ms)
        event = cls(
            id=event_id(self.meta.match_id, self.seq),
            match_id=self.meta.match_id,
            seq=self.seq,
            period=period,
            clock_ms=clock_ms,
            match_ms=self.match_ms(period, clock_ms),
            minute=minute,
            second=second,
            **fields,
        )
        self.seq += 1
        return event
