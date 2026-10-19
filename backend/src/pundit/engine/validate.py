"""Full-match validator (docs/day1-design.md section 10).

Replays every event through MatchState (so each rule exists only once), then checks the
whole-match invariants that cannot be seen one event at a time.
"""

from collections.abc import Sequence

from pundit.domain.errors import InvalidEventError
from pundit.domain.events import Event
from pundit.domain.meta import MatchMeta
from pundit.engine.rosters import load_rosters
from pundit.state.match_state import MatchState
from pundit.stats.possession import possession_timeline


def validate_match(meta: MatchMeta, events: Sequence[Event], *, strict: bool = True) -> MatchState:
    """Raise InvalidEventError on the first broken invariant; return the final state."""
    state = MatchState(meta)
    for event in events:
        state.apply(event)
    state.check_complete()
    view = state.view()

    bounds: dict[int, tuple[int, int]] = {}
    starts: dict[int, int] = {}
    for event in view.of_type("period_start", "period_end"):
        if event.type == "period_start":
            starts[event.period] = event.match_ms
        else:
            bounds[event.period] = (starts[event.period], event.match_ms)

    previous_end = -1
    for interval in possession_timeline(view):
        low, high = bounds[interval.period]
        if interval.start_ms < low or interval.end_ms > high:
            raise InvalidEventError(
                "INV-23", interval.start_event_id, "possession interval outside its period"
            )
        if interval.start_ms < previous_end:
            raise InvalidEventError(
                "INV-23", interval.start_event_id, "possession intervals overlap"
            )
        if strict and interval.duration_ms <= 0:
            raise InvalidEventError("INV-23", interval.start_event_id, "empty possession interval")
        previous_end = interval.end_ms

    if strict:
        _check_roster_names(meta)
    return state


def _check_roster_names(meta: MatchMeta) -> None:
    """INV-21: every name comes from the fictional roster."""
    known = {
        (team.id, player.shirt): player.name
        for team in load_rosters().teams
        for player in team.players
    }
    for info in meta.players.values():
        if known.get((info.team_id, info.shirt)) != info.name:
            raise InvalidEventError("INV-21", None, f"{info.id} is not in the fictional roster")
