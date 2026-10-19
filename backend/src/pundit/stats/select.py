"""Event selection: the only place that filters events for metrics (section 7.2)."""

from pundit.domain.events import Event, GoalEvent, VarCheckEvent
from pundit.domain.log import LogView
from pundit.domain.matchtime import FULL, MinuteWindow, RollingWindow


def voided_event_ids(view: LogView) -> frozenset[str]:
    """Goals overturned by VAR, together with the shots that produced them."""
    voided: set[str] = set()
    for event in view.of_type("var_check"):
        assert isinstance(event, VarCheckEvent)
        if event.check_type == "goal" and event.decision == "overturned":
            goal = view.get(event.related_ids[0])
            assert isinstance(goal, GoalEvent)
            voided.add(goal.id)
            voided.add(goal.related_ids[0])
    return frozenset(voided)


def in_window(event: Event, window: MinuteWindow | RollingWindow) -> bool:
    return window.contains(event.period, event.clock_ms, event.match_ms)


def select(
    view: LogView,
    *,
    types: tuple[str, ...],
    team: str | None = None,
    player: str | None = None,
    window: MinuteWindow | RollingWindow = FULL,
    include_voided: bool = False,
) -> list[Event]:
    voided = frozenset() if include_voided else voided_event_ids(view)
    return [
        event
        for event in view.of_type(*types)
        if (team is None or event.team == team)
        and (player is None or event.player == player)
        and in_window(event, window)
        and event.id not in voided
    ]
