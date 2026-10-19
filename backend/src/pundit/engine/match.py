"""A generated (or loaded) match: metadata plus its ordered events."""

from dataclasses import dataclass

from pundit.domain.events import Event
from pundit.domain.log import LogView
from pundit.domain.meta import MatchMeta


@dataclass(frozen=True)
class GeneratedMatch:
    meta: MatchMeta
    events: tuple[Event, ...]

    def view(self, upto: int | None = None) -> LogView:
        events = self.events if upto is None else self.events[:upto]
        return LogView.from_events(self.meta, events)
